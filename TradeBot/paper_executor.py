from __future__ import annotations

import csv
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

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
    Disabled: this app signals via the main Discord webhooks only (see send_discord_alert).

    Previously this path used Schwab quotes/option chains for simulated BTO lines.
    """
    logger.info(
        "paper_execute_trade skipped (Discord-only): symbol=%s timeframe=%s",
        getattr(signal, "symbol", None),
        getattr(signal, "timeframe", None),
    )
    return None


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
    If no true 0DTE contract is found, fall back to 1DTE.
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

    # Schwab option chain JSON is nested; we use a best-effort traversal
    # over all options, looking for calls or puts with a delta field and
    # a specific days-to-expiration (DTE).
    option_chain = chain.get("optionChain") or chain
    expirations = option_chain.get("callExpDateMap", {})
    put_expirations = option_chain.get("putExpDateMap", {})

    def _parse_dte(exp_key: str, contract: Dict[str, Any]) -> Optional[int]:
        """Best-effort extraction of DTE from contract or expiration key."""
        dte = contract.get("daysToExpiration")
        if isinstance(dte, int):
            return dte
        if isinstance(dte, str):
            try:
                return int(dte)
            except ValueError:
                pass
        # Fallback: Schwab/TDA-style keys often look like '2026-03-17:0'
        date_part = (exp_key or "").split(":", 1)[0]
        try:
            from datetime import date
            exp_date = datetime.strptime(date_part, "%Y-%m-%d").date()
            return (exp_date - date.today()).days
        except Exception:
            return None

    def _scan_for_dte(target_dtes: set[int]) -> Optional[Dict[str, Any]]:
        best_local: Optional[Dict[str, Any]] = None
        best_diff_local: Optional[float] = None

        def _scan_map(exp_map: Dict[str, Any], is_call: bool) -> None:
            nonlocal best_local, best_diff_local
            for exp_key, strikes in exp_map.items():
                for strike_key, contracts in strikes.items():
                    for c in contracts:
                        dte = _parse_dte(exp_key, c)
                        if dte is None or dte not in target_dtes:
                            continue

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
                        if best_diff_local is None or diff < best_diff_local:
                            best_diff_local = diff
                            best_local = {
                                "symbol": c.get("symbol") or c.get("optionSymbol") or "",
                                "strike": float(c.get("strikePrice")) if c.get("strikePrice") is not None else None,
                                "expiration": c.get("expirationDate") or exp_key,
                                "delta": delta_val,
                                "quote": c.get("quote") or {},
                                "dte": dte,
                            }

        if direction == "bullish":
            _scan_map(expirations, is_call=True)
        else:
            _scan_map(put_expirations, is_call=False)

        return best_local

    # First, try strict 0DTE.
    best_0dte = _scan_for_dte({0})
    if best_0dte is not None:
        return best_0dte

    # If no 0DTE contract is suitable, fall back to 1DTE.
    best_1dte = _scan_for_dte({1})
    if best_1dte is not None:
        logger.info(
            "No suitable 0DTE option found for %s; using 1DTE fallback expiring %s.",
            signal.symbol,
            best_1dte.get("expiration"),
        )
        return best_1dte

    return None

