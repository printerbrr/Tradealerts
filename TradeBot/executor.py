from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from .models import ExecutionDecision, ProposedOrder, Signal, TradeLogEntry
from .state_bridge import get_current_state

logger = logging.getLogger(__name__)

def _parse_state_timestamp(value: Any) -> Optional[datetime]:
    """Best-effort parse for timestamps stored in state snapshots."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None

    # Handle SQLite-style UTC text and ISO-like values.
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue

    try:
        # Handles microseconds and timezone offsets if present.
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _finalize_paper_approval(
    signal: Signal,
    policy: Dict[str, Any],
    schwab_snapshot: Dict[str, Any],
    state_snapshot: Dict[str, Any],
    reason: str,
) -> ExecutionDecision:
    """Shared tail: price sanity, quantity, proposed order, approved decision."""
    warnings = []
    reference_price = signal.sms_price
    schwab_price = None

    if schwab_snapshot:
        schwab_price = _extract_price_from_quote(schwab_snapshot)
        if reference_price is not None and schwab_price is not None:
            diff = abs(schwab_price - reference_price)
            pct = diff / reference_price if reference_price != 0 else 0.0
            max_pct = float(policy.get("max_price_deviation_pct", 0.02))
            if pct > max_pct:
                r = (
                    f"Price deviation too large between SMS ({reference_price}) "
                    f"and Schwab quote ({schwab_price}), deviation={pct:.4f}."
                )
                logger.info("Trade skipped: %s", r)
                return ExecutionDecision(
                    should_execute=False,
                    reason=r,
                    signal=signal,
                    policy_snapshot=dict(policy),
                    state_snapshot=state_snapshot,
                    schwab_snapshot=schwab_snapshot,
                )
        else:
            warnings.append("Could not compare SMS price to Schwab quote.")
    else:
        warnings.append("No Schwab quote provided; proceeding without quote-based checks.")

    quantity = policy.get("default_quantity")
    if not quantity:
        r = "No default_quantity specified in execution policy."
        logger.info("Trade skipped: %s", r)
        return ExecutionDecision(
            should_execute=False,
            reason=r,
            signal=signal,
            policy_snapshot=dict(policy),
            state_snapshot=state_snapshot,
            schwab_snapshot=schwab_snapshot,
        )

    side = "buy" if signal.direction.lower() == "bullish" else "sell"
    order_type = str(policy.get("order_type", "market")).lower()

    proposed = ProposedOrder(
        symbol=signal.symbol,
        side=side,
        quantity=float(quantity),
        order_type=order_type,
        time_in_force=str(policy.get("time_in_force", "DAY")),
        limit_price=policy.get("limit_price"),
        extra={},
    )

    logger.info(
        "Trade approved for %s %s x %s (%s).",
        proposed.side,
        proposed.symbol,
        proposed.quantity,
        proposed.order_type,
    )

    return ExecutionDecision(
        should_execute=True,
        reason=reason,
        signal=signal,
        proposed_order=proposed,
        warnings=warnings,
        policy_snapshot=dict(policy),
        state_snapshot=state_snapshot,
        schwab_snapshot=schwab_snapshot,
    )


def decide_trade(
    signal: Signal,
    policy: Dict[str, Any],
    schwab_quote: Optional[Dict[str, Any]] = None,
) -> ExecutionDecision:
    """
    Core decision engine for whether a given signal should result in an order.

    Parameters
    ----------
    signal:
        Normalized signal from TradeAlerts.
    policy:
        Execution policy configuration. This is intentionally loose for now and
        will be refined as you finalize sizing, caps, order types, and limits.
    schwab_quote:
        Optional fresh quote obtained from Schwab for the symbol.
    """

    # For this phase, we only consider 5MIN MACD crossover signals with
    # strict multi-timeframe confluence. The signal's raw_data should
    # indicate that it is a MACD-based alert on the 5MIN timeframe.
    tf = (signal.timeframe or "").upper()
    if tf != "5MIN":
        reason = "Only 5MIN signals are eligible for paper trading."
        logger.info("Trade skipped: %s", reason)
        return ExecutionDecision(
            should_execute=False,
            reason=reason,
            signal=signal,
            policy_snapshot=dict(policy),
            state_snapshot={},
            schwab_snapshot=schwab_quote or {},
        )

    raw = signal.raw_data or {}
    if raw.get("action") != "macd_crossover":
        reason = "Only MACD crossover alerts are eligible for paper trading."
        logger.info("Trade skipped: %s", reason)
        return ExecutionDecision(
            should_execute=False,
            reason=reason,
            signal=signal,
            policy_snapshot=dict(policy),
            state_snapshot={},
            schwab_snapshot=schwab_quote or {},
        )

    schwab_snapshot = schwab_quote or {}

    enabled = bool(policy.get("enabled", False))
    if not enabled:
        reason = "Execution disabled by policy (policy.enabled is False or missing)."
        logger.info("Trade skipped: %s", reason)
        return ExecutionDecision(
            should_execute=False,
            reason=reason,
            signal=signal,
            policy_snapshot=dict(policy),
            state_snapshot={},
            schwab_snapshot=schwab_snapshot,
        )

    direction = (signal.direction or "").lower()
    if direction not in ("bullish", "bearish"):
        reason = "Signal direction must be bullish or bearish."
        logger.info("Trade skipped: %s", reason)
        return ExecutionDecision(
            should_execute=False,
            reason=reason,
            signal=signal,
            policy_snapshot=dict(policy),
            state_snapshot={},
            schwab_snapshot=schwab_snapshot,
        )

    # Default: any 5MIN MACD crossover is enough (testing / option-picker validation).
    # Set PAPER_TRADE_STRICT_CONFLUENCE=1 for 1H/4H/15m/1m multi-timeframe rules.
    strict_confluence = os.getenv("PAPER_TRADE_STRICT_CONFLUENCE", "0") == "1"
    if not strict_confluence:
        state_snapshot = {"5MIN": get_current_state(signal.symbol, "5MIN")}
        logger.info(
            "Paper trade: simple 5MIN MACD mode (set PAPER_TRADE_STRICT_CONFLUENCE=1 for full confluence)."
        )
        return _finalize_paper_approval(
            signal,
            policy,
            schwab_snapshot,
            state_snapshot,
            "Execution approved: 5MIN MACD crossover (simple mode).",
        )

    state_snapshot = {
        "1MIN": get_current_state(signal.symbol, "1MIN"),
        "5MIN": get_current_state(signal.symbol, "5MIN"),
        "15MIN": get_current_state(signal.symbol, "15MIN"),
    }

    def _matches(dir_str: str, value: Optional[str]) -> bool:
        if not value:
            return False
        v = value.upper()
        if dir_str == "bullish":
            return v == "BULLISH"
        if dir_str == "bearish":
            return v == "BEARISH"
        return False

    state_snapshot["1HR"] = get_current_state(signal.symbol, "1HR")
    state_snapshot["4HR"] = get_current_state(signal.symbol, "4HR")

    s1 = state_snapshot["1MIN"]
    s5 = state_snapshot["5MIN"]
    s15 = state_snapshot["15MIN"]
    s1h = state_snapshot["1HR"]
    s4h = state_snapshot["4HR"]

    # 5MIN MACD cross is the trigger and must match signal direction.
    if not _matches(direction, s5.get("macd_status")):
        reason = "5MIN MACD state does not match the signal direction."
        logger.info("Trade skipped: %s", reason)
        return ExecutionDecision(
            should_execute=False,
            reason=reason,
            signal=signal,
            policy_snapshot=dict(policy),
            state_snapshot=state_snapshot,
            schwab_snapshot=schwab_snapshot,
        )

    # Higher-timeframe trend confluence: 1H + 4H EMA must align.
    if not _matches(direction, s1h.get("ema_status")):
        reason = "1H EMA state does not match the signal direction."
        logger.info("Trade skipped: %s", reason)
        return ExecutionDecision(
            should_execute=False,
            reason=reason,
            signal=signal,
            policy_snapshot=dict(policy),
            state_snapshot=state_snapshot,
            schwab_snapshot=schwab_snapshot,
        )

    if not _matches(direction, s4h.get("ema_status")):
        reason = "4H EMA state does not match the signal direction."
        logger.info("Trade skipped: %s", reason)
        return ExecutionDecision(
            should_execute=False,
            reason=reason,
            signal=signal,
            policy_snapshot=dict(policy),
            state_snapshot=state_snapshot,
            schwab_snapshot=schwab_snapshot,
        )

    # 15MIN EMA must match and be a recent cross.
    if not _matches(direction, s15.get("ema_status")):
        reason = "15MIN EMA state does not match the signal direction."
        logger.info("Trade skipped: %s", reason)
        return ExecutionDecision(
            should_execute=False,
            reason=reason,
            signal=signal,
            policy_snapshot=dict(policy),
            state_snapshot=state_snapshot,
            schwab_snapshot=schwab_snapshot,
        )
    recent_15m_ema_bars = int(policy.get("recent_15m_ema_bars", 3))
    last_15m_ema_ts = _parse_state_timestamp(s15.get("last_ema_update"))
    if last_15m_ema_ts is None:
        reason = "15MIN EMA recency check failed (missing last_ema_update)."
        logger.info("Trade skipped: %s", reason)
        return ExecutionDecision(
            should_execute=False,
            reason=reason,
            signal=signal,
            policy_snapshot=dict(policy),
            state_snapshot=state_snapshot,
            schwab_snapshot=schwab_snapshot,
        )
    if datetime.utcnow() - last_15m_ema_ts > timedelta(minutes=15 * recent_15m_ema_bars):
        reason = (
            f"15MIN EMA cross is not recent enough "
            f"(older than {recent_15m_ema_bars} bars)."
        )
        logger.info("Trade skipped: %s", reason)
        return ExecutionDecision(
            should_execute=False,
            reason=reason,
            signal=signal,
            policy_snapshot=dict(policy),
            state_snapshot=state_snapshot,
            schwab_snapshot=schwab_snapshot,
        )

    # 1MIN MACD must match and be within 2 bars by default.
    if not _matches(direction, s1.get("macd_status")):
        reason = "1MIN MACD state does not match the signal direction."
        logger.info("Trade skipped: %s", reason)
        return ExecutionDecision(
            should_execute=False,
            reason=reason,
            signal=signal,
            policy_snapshot=dict(policy),
            state_snapshot=state_snapshot,
            schwab_snapshot=schwab_snapshot,
        )
    one_min_macd_bars = int(policy.get("one_min_macd_bars", 2))
    last_1m_macd_ts = _parse_state_timestamp(s1.get("last_macd_update"))
    if last_1m_macd_ts is None:
        reason = "1MIN MACD recency check failed (missing last_macd_update)."
        logger.info("Trade skipped: %s", reason)
        return ExecutionDecision(
            should_execute=False,
            reason=reason,
            signal=signal,
            policy_snapshot=dict(policy),
            state_snapshot=state_snapshot,
            schwab_snapshot=schwab_snapshot,
        )
    if datetime.utcnow() - last_1m_macd_ts > timedelta(minutes=one_min_macd_bars):
        reason = (
            f"1MIN MACD cross is not within {one_min_macd_bars} bars."
        )
        logger.info("Trade skipped: %s", reason)
        return ExecutionDecision(
            should_execute=False,
            reason=reason,
            signal=signal,
            policy_snapshot=dict(policy),
            state_snapshot=state_snapshot,
            schwab_snapshot=schwab_snapshot,
        )

    return _finalize_paper_approval(
        signal,
        policy,
        schwab_snapshot,
        state_snapshot,
        "Execution approved by paper-trade confluence policy and price checks.",
    )


def execute_trade(
    decision: ExecutionDecision,
    schwab_client: Any,
    policy: Dict[str, Any],
) -> Optional[TradeLogEntry]:
    """
    Live broker execution is intentionally disabled.

    TradeAlerts only posts to Discord webhooks; no orders are sent to any broker.
    """
    logger.info(
        "execute_trade ignored (Discord-only deployment): %s",
        decision.reason,
    )
    return None


def _extract_price_from_quote(quote: Dict[str, Any]) -> Optional[float]:
    """
    Best-effort extraction of a last/mark price from a Schwab quote payload.

    This will be updated once the exact Schwab quote schema is known.
    """

    # These keys are placeholders and may need to be adapted to Schwab's schema.
    for key in ("lastPrice", "last_price", "mark", "markPrice", "bid", "ask"):
        value = quote.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None

