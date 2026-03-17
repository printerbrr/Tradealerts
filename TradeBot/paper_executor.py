from __future__ import annotations

import csv
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from .executor import decide_trade
from .models import Signal, TradeLogEntry
from .schwab_client import SchwabClient

logger = logging.getLogger(__name__)

# 1-minute channel webhook for paper-trade BTO alerts (env override supported)
PAPER_TRADE_DISCORD_WEBHOOK = os.getenv(
    "PAPER_TRADE_DISCORD_WEBHOOK",
    "https://discord.com/api/webhooks/1451348339970019599/fXfdaUAUGxyavnSeY9oCUv6KQ5GBvSKwpdmhqCk7IX4HiFrj22FPwloBvjTghiI7KRze",
)


def paper_execute_trade(
    signal: Signal,
    policy: Dict[str, Any],
    client: Optional[SchwabClient] = None,
    csv_path: Optional[str] = None,
) -> Optional[TradeLogEntry]:
    """
    Paper-trading execution path.

    - Fetches a fresh Schwab quote for the symbol.
    - Runs the standard decision engine.
    - If approved, selects 0DTE option with delta closest to +/-0.20 and sends
      a Discord alert in format: BTO {strike}{C|P} @ {mark}
    - Optionally appends a row to a CSV file if csv_path is set.

    No live orders are sent.
    """

    client = client or SchwabClient()

    # Underlying quote for sanity checks and entry reference
    try:
        schwab_quote = client.get_quote(signal.symbol)
    except Exception as exc:
        logger.exception("Failed to fetch Schwab quote for %s: %s", signal.symbol, exc)
        schwab_quote = {}

    decision = decide_trade(signal=signal, policy=policy, schwab_quote=schwab_quote)

    if not decision.should_execute:
        logger.info("Paper trade skipped: %s", decision.reason)
        return None

    # Select a 0DTE option contract with delta closest to +/-0.20.
    option_info = _select_0dte_option_for_signal(signal, client)
    if option_info is None:
        logger.info("Paper trade skipped: no suitable 0DTE option found.")
        return None

    # Build a TradeLogEntry (simulated execution).
    entry_time = datetime.utcnow()
    schwab_snapshot = option_info.get("quote") or decision.schwab_snapshot or schwab_quote or {}

    mark_price = _best_effort_mark_price(schwab_snapshot)
    last_price = _best_effort_last_price(schwab_snapshot)

    log_entry = TradeLogEntry(
        symbol=option_info["symbol"],
        timeframe=signal.timeframe,
        tag=signal.tag,
        direction=signal.direction,
        size=1.0,
        order_id=None,
        entry_requested_at=entry_time,
        filled_at=entry_time,  # For paper trades, treat entry time as fill time.
        requested_price=mark_price or last_price or signal.sms_price,
        filled_price=mark_price or last_price or signal.sms_price,
        signal_snapshot=signal.to_dict(),
        schwab_snapshot=schwab_snapshot,
        decision_reason=decision.reason,
        policy_version=str(policy.get("policy_version"))
        if policy.get("policy_version") is not None
        else None,
        additional_info={
            "paper_trade": True,
            "warnings": decision.warnings,
            "underlying_symbol": signal.symbol,
            "option_strike": option_info.get("strike"),
            "option_expiration": option_info.get("expiration"),
            "option_delta": option_info.get("delta"),
        },
    )

    # Send BTO alert to Discord (1-minute channel)
    _send_paper_trade_discord_alert(
        strike=option_info.get("strike"),
        direction=signal.direction,
        mark=mark_price or last_price,
    )

    if csv_path:
        _append_to_csv(log_entry, csv_path)

    logger.info(
        "Paper trade logged for %s %s x %s.",
        log_entry.direction,
        log_entry.symbol,
        log_entry.size,
    )

    return log_entry


def _send_paper_trade_discord_alert(
    strike: Optional[float],
    direction: str,
    mark: Optional[float],
) -> None:
    """
    Post a single line to the paper-trade Discord webhook:
    BTO {strike}{C|P} @ {mark}
    e.g. BTO 670C @ 1.21
    """
    if not PAPER_TRADE_DISCORD_WEBHOOK:
        logger.warning("PAPER_TRADE_DISCORD_WEBHOOK not set; skipping Discord alert.")
        return
    strike_str = str(int(round(strike))) if strike is not None else "?"
    side = "C" if (direction or "").lower() == "bullish" else "P"
    mark_str = f"{mark:.2f}" if mark is not None else "?"
    message = f"BTO {strike_str}{side} @ {mark_str}"
    try:
        with httpx.Client() as http:
            r = http.post(
                PAPER_TRADE_DISCORD_WEBHOOK,
                json={"content": message},
                timeout=10.0,
            )
            r.raise_for_status()
    except Exception as exc:
        logger.exception("Failed to send paper-trade Discord alert: %s", exc)


def _append_to_csv(entry: TradeLogEntry, csv_path: str) -> None:
    """
    Append a TradeLogEntry to a CSV file, creating headers if needed.
    """

    path = Path(csv_path)
    is_new = not path.exists()

    fieldnames = [
        "entry_requested_at_utc",
        "filled_at_utc",
        "symbol",
        "timeframe",
        "tag",
        "direction",
        "size",
        "requested_price",
        "filled_price",
        "decision_reason",
        "ema_status",
        "macd_status",
        "schwab_mark",
        "schwab_last",
        "underlying_symbol",
        "option_strike",
        "option_expiration",
        "option_delta",
    ]

    state = entry.signal_snapshot.get("raw_data") or {}
    ema_status = state.get("ema_status") or entry.schwab_snapshot.get("ema_status")
    macd_status = state.get("macd_status") or entry.schwab_snapshot.get("macd_status")

    schwab_mark = _best_effort_mark_price(entry.schwab_snapshot)
    schwab_last = _best_effort_last_price(entry.schwab_snapshot)

    row = {
        "entry_requested_at_utc": entry.entry_requested_at.isoformat()
        if entry.entry_requested_at
        else "",
        "filled_at_utc": entry.filled_at.isoformat() if entry.filled_at else "",
        "symbol": entry.symbol,
        "timeframe": entry.timeframe,
        "tag": entry.tag,
        "direction": entry.direction,
        "size": entry.size,
        "requested_price": entry.requested_price,
        "filled_price": entry.filled_price,
        "decision_reason": entry.decision_reason,
        "ema_status": ema_status,
        "macd_status": macd_status,
        "schwab_mark": schwab_mark,
        "schwab_last": schwab_last,
        "underlying_symbol": entry.additional_info.get("underlying_symbol", ""),
        "option_strike": entry.additional_info.get("option_strike", ""),
        "option_expiration": entry.additional_info.get("option_expiration", ""),
        "option_delta": entry.additional_info.get("option_delta", ""),
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open(mode="a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def _best_effort_mark_price(snapshot: Dict[str, Any]) -> Optional[float]:
    quote = snapshot.get("quote") or snapshot
    for key in ("mark", "markPrice", "regularMarketLastPrice"):
        value = quote.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _best_effort_last_price(snapshot: Dict[str, Any]) -> Optional[float]:
    quote = snapshot.get("quote") or snapshot
    for key in ("lastPrice", "last_price", "regularMarketLastPrice"):
        value = quote.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _select_0dte_option_for_signal(
    signal: Signal, client: SchwabClient
) -> Optional[Dict[str, Any]]:
    """
    Pick a 0DTE option with delta closest to +/-0.20, depending on direction.
    """

    try:
        chain = client.get_option_chain_0dte(signal.symbol)
    except Exception as exc:
        logger.exception("Failed to fetch option chain for %s: %s", signal.symbol, exc)
        return None

    direction = (signal.direction or "").lower()
    if direction not in ("bullish", "bearish"):
        return None

    target_delta = 0.20 if direction == "bullish" else -0.20

    best: Optional[Dict[str, Any]] = None
    best_diff: Optional[float] = None

    # Schwab option chain JSON is nested; we use a best-effort traversal
    # over all options, looking for calls or puts with a delta field.
    option_chain = chain.get("optionChain") or chain
    expirations = option_chain.get("callExpDateMap", {})
    put_expirations = option_chain.get("putExpDateMap", {})

    def _scan_map(exp_map: Dict[str, Any], is_call: bool) -> None:
        nonlocal best, best_diff
        for exp_key, strikes in exp_map.items():
            for strike_key, contracts in strikes.items():
                for c in contracts:
                    option_type = c.get("putCall")
                    if is_call and option_type != "CALL":
                        continue
                    if not is_call and option_type != "PUT":
                        continue
                    delta = c.get("delta")
                    try:
                        if delta is None:
                            continue
                        delta_val = float(delta)
                    except (TypeError, ValueError):
                        continue
                    diff = abs(delta_val - target_delta)
                    if best_diff is None or diff < best_diff:
                        best_diff = diff
                        best = {
                            "symbol": c.get("symbol") or c.get("optionSymbol") or "",
                            "strike": float(c.get("strikePrice")) if c.get("strikePrice") is not None else None,
                            "expiration": c.get("expirationDate") or exp_key,
                            "delta": delta_val,
                            "quote": c.get("quote") or {},
                        }

    if direction == "bullish":
        _scan_map(expirations, is_call=True)
    else:
        _scan_map(put_expirations, is_call=False)

    return best

