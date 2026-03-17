from __future__ import annotations

from typing import Any, Dict

from state_manager import state_manager


def get_current_state(symbol: str, timeframe: str) -> Dict[str, Any]:
    """
    Fetch the current EMA/MACD state for a symbol and timeframe.

    This calls into the existing state_manager and normalizes the
    response into a simple dictionary that can be attached to
    ExecutionDecision and TradeLogEntry instances.
    """

    states = state_manager.get_all_states(symbol.upper())
    tf_key = timeframe.upper()
    tf_state = states.get(tf_key, {})

    # Keep this intentionally loose; caller can choose which keys it cares about.
    return {
        "symbol": symbol.upper(),
        "timeframe": tf_key,
        "ema_status": tf_state.get("ema_status"),
        "macd_status": tf_state.get("macd_status"),
        "last_ema_update": tf_state.get("last_ema_update"),
        "last_macd_update": tf_state.get("last_macd_update"),
        "last_ema_price": tf_state.get("last_ema_price"),
        "last_macd_price": tf_state.get("last_macd_price"),
    }


def is_state_consistent_with_signal(
    signal_direction: str, state: Dict[str, Any]
) -> bool:
    """
    Basic sanity check: confirm that the current EMA/MACD state
    is consistent with the intended trade direction.

    For now this is deliberately minimal and conservative. As we
    refine the rules, we can incorporate timeframe-specific or
    tag-specific logic here.
    """

    ema_status = (state.get("ema_status") or "").upper()
    macd_status = (state.get("macd_status") or "").upper()
    direction = signal_direction.lower()

    if direction == "bullish":
        return ema_status == "BULLISH" or macd_status == "BULLISH"
    if direction == "bearish":
        return ema_status == "BEARISH" or macd_status == "BEARISH"

    # If we do not recognize the direction, fail closed.
    return False

