from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class Signal:
    """
    Normalized representation of an approved TradeAlerts signal.

    This should be constructed in TradeAlerts (e.g. in main.py) once
    analyze_data() has determined that an alert should fire.
    """

    symbol: str
    timeframe: str
    tag: str
    direction: str  # e.g. "bullish" or "bearish"
    sms_price: Optional[float] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    raw_data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["timestamp"] = self.timestamp.isoformat()
        return data


@dataclass
class ProposedOrder:
    """
    High-level representation of an order the bot intends to place.

    This is intentionally generic; a lower-level adapter will translate
    it into Schwab's specific order JSON when calling the API.
    """

    symbol: str  # underlying or option symbol, depending on use
    side: str  # "buy" or "sell"
    quantity: float  # shares or contracts
    order_type: str  # e.g. "market", "limit"
    time_in_force: str = "DAY"
    limit_price: Optional[float] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionDecision:
    """
    Outcome of the decision engine for a given signal.
    """

    should_execute: bool
    reason: str
    signal: Signal
    proposed_order: Optional[ProposedOrder] = None
    warnings: List[str] = field(default_factory=list)
    policy_snapshot: Dict[str, Any] = field(default_factory=dict)
    state_snapshot: Dict[str, Any] = field(default_factory=dict)
    schwab_snapshot: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "should_execute": self.should_execute,
            "reason": self.reason,
            "signal": self.signal.to_dict(),
            "proposed_order": asdict(self.proposed_order)
            if self.proposed_order is not None
            else None,
            "warnings": list(self.warnings),
            "policy_snapshot": dict(self.policy_snapshot),
            "state_snapshot": dict(self.state_snapshot),
            "schwab_snapshot": dict(self.schwab_snapshot),
        }


@dataclass
class TradeLogEntry:
    """
    Persistent record of an executed trade.

    This is designed for after-the-fact review as well as debugging.
    """

    symbol: str
    timeframe: str
    tag: str
    direction: str
    size: float

    order_id: Optional[str] = None

    entry_requested_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None

    requested_price: Optional[float] = None
    filled_price: Optional[float] = None

    signal_snapshot: Dict[str, Any] = field(default_factory=dict)
    schwab_snapshot: Dict[str, Any] = field(default_factory=dict)
    decision_reason: str = ""
    policy_version: Optional[str] = None

    additional_info: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "tag": self.tag,
            "direction": self.direction,
            "size": self.size,
            "order_id": self.order_id,
            "entry_requested_at": self.entry_requested_at.isoformat()
            if self.entry_requested_at
            else None,
            "filled_at": self.filled_at.isoformat() if self.filled_at else None,
            "requested_price": self.requested_price,
            "filled_price": self.filled_price,
            "signal_snapshot": dict(self.signal_snapshot),
            "schwab_snapshot": dict(self.schwab_snapshot),
            "decision_reason": self.decision_reason,
            "policy_version": self.policy_version,
            "additional_info": dict(self.additional_info),
        }

