## TradeBot Framework Overview

This folder contains a **Schwab-enabled trading framework** that plugs into your existing TradeAlerts system without changing how signals are generated. TradeAlerts remains responsible for **signal detection and filtering**; TradeBot is responsible for **turning approved signals into well-logged Schwab orders**.

### High-level flow

```mermaid
flowchart LR
  Signal[Signal from TradeAlerts analyze_data] --> BuildSignal[Build TradeBot Signal model]
  BuildSignal --> FetchState[Read EMA/MACD from state_manager]
  BuildSignal --> FetchQuote[Fetch fresh Schwab quote]
  FetchState --> Decide[TradeBot.decide_trade]
  FetchQuote --> Decide
  Decide -->|should_execute=false| Skip[Log skip / reason only]
  Decide -->|should_execute=true| Order[TradeBot.execute_trade (Schwab API)]
  Order --> TradeLog[Record TradeLogEntry in persistent store]
```

- **Signal origin**: TradeAlerts `main.py` calls into TradeBot *after* `analyze_data` approves a signal.
- **State confirmation**: TradeBot re-reads EMA/MACD from the existing `state_manager` before any order.
- **Market confirmation**: TradeBot fetches a **fresh Schwab quote** before deciding to trade.
- **Decision layer**: TradeBot decides whether to trade based on policy (sizing, caps, order types, trade limits – to be filled in later).
- **Execution**: TradeBot sends an order to Schwab (via a dedicated client module).
- **Trade logging**: Every executed trade is written as a detailed `TradeLogEntry` for later review.

TradeBot is designed as a **pure Python library**. It does not run its own FastAPI app; TradeAlerts remains your web server and simply imports and calls TradeBot.

---

## Modules in this folder

### `models.py`

Defines the core data structures that move through the framework:

- **`Signal`**: The normalized representation of an approved TradeAlerts signal.
  - Fields (subject to change as we integrate with `main.py`):
    - `symbol`: underlying symbol (e.g. `SPY`).
    - `timeframe`: textual timeframe (e.g. `15MIN`).
    - `tag`: final tag string (e.g. `CALL15`, `C30`, `Call30`, `SQZ15`).
    - `direction`: high-level direction such as `bullish` or `bearish`.
    - `sms_price`: price parsed from SMS (MARK at signal time).
    - `timestamp`: server-side timestamp when signal was processed.
    - `raw_data`: optional dictionary for any extra fields carried over from TradeAlerts.

- **`ExecutionDecision`**: The result of TradeBot’s decision engine.
  - Indicates whether a trade should be placed and why:
    - `should_execute`: bool.
    - `reason`: human-readable explanation (e.g. “CALL15 with EMA/MACD aligned; quote within slippage bounds; risk checks passed”).
    - `proposed_order`: normalized order specification to be translated to Schwab’s API.
    - `warnings`: list of notable conditions (e.g. “close to daily trade cap”, “spread wider than X%”).

- **`TradeLogEntry`**: A persistent record of every **executed** trade.
  - This is central to your later review and analytics:
    - `symbol`, `timeframe`, `tag`, `direction`, `size`.
    - `entry_requested_at`: when the order was sent to Schwab.
    - `filled_at`: when Schwab reported a fill (if available).
    - `requested_price`: price used when submitting the order (e.g. limit price or reference MARK).
    - `filled_price`: final execution price (if filled).
    - `signal_snapshot`: copy of the `Signal` plus EMA/MACD states used at decision time.
    - `schwab_snapshot`: key quote/account fields used when deciding to trade.
    - `decision_reason`: the same reasoning used in `ExecutionDecision`.
    - `policy_version`: optional string/hash of the policy configuration.
    - `order_id`: Schwab’s order identifier.

### `schwab_client.py`

Wrapper around the Schwab Trader API. Intent:

- Manage **OAuth** (using `schwab-py` or direct REST calls).
- Provide simple, strongly-typed methods:
  - `get_quote(symbol)`.
  - `get_account_info()` / `get_positions()`.
  - `place_order(order_spec)` returning an order id or full response.
- Hide token refresh, base URLs, and low-level HTTP details from the rest of TradeBot.

This module will be the only place that knows Schwab’s exact request/response shapes.

### `state_bridge.py`

Bridges TradeBot to your existing `state_manager`:

- Reads EMA/MACD status for a symbol/timeframe from the `market_states.db` used by TradeAlerts.
- Provides helpers such as:
  - `get_current_state(symbol, timeframe) -> dict`.
  - `is_state_consistent_with_signal(signal, state) -> bool`.

This ensures that just before an order is sent, the **live state** still agrees with the original signal.

### `executor.py`

Contains the two core functions used by TradeAlerts:

- `decide_trade(signal, policy, state_snapshot, schwab_quote) -> ExecutionDecision`
  - Applies the execution policy (sizing, caps, order types, trade limits – currently placeholders).
  - Verifies:
    - EMA/MACD confluence and direction, using `state_bridge`.
    - Price sanity between SMS MARK and Schwab quote.
    - Basic safety rules (max trades per symbol/day, etc., once specified).

- `execute_trade(decision, schwab_client, policy) -> TradeLogEntry | None`
  - If `decision.should_execute` is `False`, returns early and logs the reason.
  - If `True`:
    - Builds the Schwab order payload from `decision.proposed_order`.
    - Sends it via `schwab_client.place_order`.
    - Captures:
      - `entry_requested_at` timestamp.
      - Schwab order id and any immediate status.
    - Optionally polls or hooks into a later status check to fill in:
      - `filled_at`.
      - `filled_price`.
    - Returns a fully-populated `TradeLogEntry` that the caller can persist.

### `logging_utils.py` / `trade_log_store.py` (planned)

Dedicated helpers for:

- Structured, human-readable logs to your existing `trade_alerts.log` (or a dedicated TradeBot log).
- Persistent storage of `TradeLogEntry` instances:
  - Likely a small SQLite table (e.g. `executed_trades`) for easy querying.
  - Optionally an append-only JSON or CSV log for simple out-of-band analysis.

---

## Trade logging expectations

For **every order that is actually sent to Schwab**, TradeBot will create a corresponding `TradeLogEntry` that captures:

- **Order entry time**: when `place_order` is called.
- **Order fill time**: when a fill is detected from Schwab (may arrive slightly later).
- **Fill price**: the final execution price as reported by Schwab.
- **Reasoning for the trade**:
  - Signal tag and direction.
  - EMA/MACD state at decision time.
  - Schwab quote used for confirmation.
  - Execution policy checks that passed (and any relevant parameters like target size, caps, or order type).

These logs are intended for **after-the-fact review**, not just debugging, so the design emphasizes human-readable explanations in addition to raw numbers.

---

## Integration with TradeAlerts

When we wire this into `main.py`, the typical call sequence will be:

1. TradeAlerts receives SMS, parses it, and updates `state_manager` as usual.
2. `analyze_data(parsed_data)` returns `True` (signal approved).
3. TradeAlerts constructs a `TradeBot.Signal`:
   - Symbol, timeframe, final tag, direction, SMS MARK, and timestamp.
4. TradeAlerts asks TradeBot to:
   - Read the **current** EMA/MACD state for that symbol/timeframe.
   - Fetch a **fresh** Schwab quote.
   - Run `decide_trade` and, if approved, `execute_trade`.
5. TradeBot returns:
   - An `ExecutionDecision` (for logging even if no trade).
   - Optionally a `TradeLogEntry` if an order was actually placed.

All of this happens without changing how alerts are sent to Discord; Discord remains a separate “subscriber” to the same signals.

