from __future__ import annotations

import logging
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

    state_snapshot = {
        "1MIN": get_current_state(signal.symbol, "1MIN"),
        "5MIN": get_current_state(signal.symbol, "5MIN"),
        "15MIN": get_current_state(signal.symbol, "15MIN"),
    }
    schwab_snapshot = schwab_quote or {}

    # Placeholder: these will be replaced by concrete policy-driven checks.
    enabled = bool(policy.get("enabled", False))
    if not enabled:
        reason = "Execution disabled by policy (policy.enabled is False or missing)."
        logger.info("Trade skipped: %s", reason)
        return ExecutionDecision(
            should_execute=False,
            reason=reason,
            signal=signal,
            policy_snapshot=dict(policy),
            state_snapshot=state_snapshot,
            schwab_snapshot=schwab_snapshot,
        )

    # Confirm paper-trading setup:
    # CALL:
    #   - 1H and 4H EMA are bullish
    #   - 15MIN EMA bullish cross happened recently
    #   - 5MIN MACD bullish cross is the trigger signal
    #   - 1MIN MACD bullish cross happened within 2 bars
    # PUT is the exact opposite.
    direction = (signal.direction or "").lower()
    if direction not in ("bullish", "bearish"):
        reason = "Signal direction must be bullish or bearish."
        logger.info("Trade skipped: %s", reason)
        return ExecutionDecision(
            should_execute=False,
            reason=reason,
            signal=signal,
            policy_snapshot=dict(policy),
            state_snapshot=state_snapshot,
            schwab_snapshot=schwab_snapshot,
        )

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

    # Basic price sanity check using Schwab quote if available.
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
                reason = (
                    f"Price deviation too large between SMS ({reference_price}) "
                    f"and Schwab quote ({schwab_price}), deviation={pct:.4f}."
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
        else:
            warnings.append("Could not compare SMS price to Schwab quote.")
    else:
        warnings.append("No Schwab quote provided; proceeding without quote-based checks.")

    # Placeholder sizing logic: the real rules will be filled in later.
    # For now we require an explicit quantity in policy to avoid surprises.
    quantity = policy.get("default_quantity")
    if not quantity:
        reason = "No default_quantity specified in execution policy."
        logger.info("Trade skipped: %s", reason)
        return ExecutionDecision(
            should_execute=False,
            reason=reason,
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

    reason = "Execution approved by paper-trade confluence policy and price checks."
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


def execute_trade(
    decision: ExecutionDecision,
    schwab_client: Any,
    policy: Dict[str, Any],
) -> Optional[TradeLogEntry]:
    """
    Execute an approved trade decision via the Schwab client.

    Returns a TradeLogEntry if an order was actually sent, or None if not.

    This function assumes that the caller has already checked
    decision.should_execute and is passing in a ready Schwab client
    (with authentication handled elsewhere).
    """

    if not decision.should_execute or decision.proposed_order is None:
        logger.info(
            "execute_trade called with non-executable decision: %s", decision.reason
        )
        return None

    entry_time = datetime.utcnow()

    # Call out to the Schwab client. The exact shape of the response will depend
    # on the client implementation; we keep this generic for now.
    try:
        response = schwab_client.place_order(decision.proposed_order, policy=policy)
    except Exception as exc:
        logger.exception("Error while placing Schwab order: %s", exc)
        return None

    order_id = getattr(response, "order_id", None) or response.get("order_id") if isinstance(
        response, dict
    ) else None

    # Fill information may or may not be available immediately.
    filled_at = None
    filled_price = None
    if isinstance(response, dict):
        filled_at_raw = response.get("filled_at") or response.get("fill_time")
        filled_price = response.get("filled_price")
        if isinstance(filled_at_raw, datetime):
            filled_at = filled_at_raw

    log_entry = TradeLogEntry(
        symbol=decision.signal.symbol,
        timeframe=decision.signal.timeframe,
        tag=decision.signal.tag,
        direction=decision.signal.direction,
        size=decision.proposed_order.quantity,
        order_id=str(order_id) if order_id is not None else None,
        entry_requested_at=entry_time,
        filled_at=filled_at,
        requested_price=decision.proposed_order.limit_price
        or decision.signal.sms_price,
        filled_price=filled_price,
        signal_snapshot=decision.signal.to_dict(),
        schwab_snapshot=dict(decision.schwab_snapshot),
        decision_reason=decision.reason,
        policy_version=str(policy.get("policy_version"))
        if policy.get("policy_version") is not None
        else None,
        additional_info={"raw_response": response},
    )

    logger.info(
        "Order sent for %s %s x %s; order_id=%s",
        log_entry.direction,
        log_entry.symbol,
        log_entry.size,
        log_entry.order_id,
    )

    return log_entry


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

