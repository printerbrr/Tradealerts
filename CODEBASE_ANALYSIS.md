# Comprehensive Codebase Analysis: Trade Alerts System

## Table of Contents
1. [System Overview](#system-overview)
2. [Architecture & Components](#architecture--components)
3. [Signal Types & Meanings](#signal-types--meanings)
4. [Signal Generation Flow](#signal-generation-flow)
5. [Signal Filtering Logic](#signal-filtering-logic)
6. [Discord Webhook Delivery](#discord-webhook-delivery)
7. [State Management](#state-management)
8. [Alert Toggle System](#alert-toggle-system)

---

## System Overview

This is a **SMS-based trade alerting system** that:
- Receives SMS messages forwarded from Tasker (Android automation)
- Parses trading signals (EMA crossovers, MACD crossovers, Squeeze Firing)
- Tracks market state across multiple timeframes (1MIN, 5MIN, 15MIN, 30MIN, 1HR, 2HR, 4HR, 1DAY)
- Filters signals based on confluence rules and time windows
- Sends formatted alerts to Discord webhooks per symbol
- Manages per-symbol alert toggles for fine-grained control

**Key Technologies:**
- FastAPI (Python web framework)
- SQLite (state persistence)
- Discord Webhooks (alert delivery)
- Tasker (Android SMS forwarding)

---

## Architecture & Components

### Core Modules

#### 1. **main.py** - Main Application
- FastAPI web server
- SMS webhook endpoint (`/webhook/sms`)
- Signal parsing (`parse_sms_data()`)
- Signal analysis (`analyze_data()`)
- Discord alert formatting and sending (`send_discord_alert()`)
- State updates (`update_system_state()`)

#### 2. **state_manager.py** - State Tracking
- Manages EMA and MACD crossover states per symbol/timeframe
- SQLite database: `market_states.db`
- Tables:
  - `timeframe_states`: Current EMA/MACD status per timeframe
  - `state_history`: Historical crossover events
  - `system_metadata`: System-level tracking
  - `alert_toggles`: Per-symbol alert tag enable/disable

#### 3. **webhook_manager.py** - Webhook Management
- Manages Discord webhook URLs per symbol
- JSON config: `discord_webhooks.json`
- Supports symbol-specific webhooks with fallback to default
- Separate price alert webhook support

#### 4. **confluence_rules.py** - Confluence Rules Engine
- Configurable rules for signal filtering
- JSON config: `confluence_rules.json`
- Currently not actively used (rules disabled by default)

#### 5. **alert_toggle_manager.py** - Alert Toggle System
- Per-symbol, per-tag alert enable/disable
- Stored in SQLite `alert_toggles` table
- Supports tags like: `C15`, `CALL15`, `Call15`, `P30`, `PUT30`, `Put30`, `SQZ15`, etc.

---

## Signal Types & Meanings

Based on `Signal Meanings 10.22.25.txt` and code analysis:

### 1. **C# / P# Signals** (Cyan/Magenta)
**Meaning:** EMA crossover on THAT timeframe (no higher timeframe confluence required)

**Examples:**
- `C30` = 30min EMA bullish crossover (Cyan)
- `P30` = 30min EMA bearish crossover (Magenta)

**Generation Logic:**
- Triggered by EMA crossover on current timeframe
- Next higher timeframe EMA status is **NOT** aligned with crossover direction
- Tag format: `C{timeframe}` or `P{timeframe}` (e.g., `C15`, `P30`, `C1H`, `P1D`)

### 2. **CALL# / PUT# Signals** (Cyan/Magenta)
**Meaning:** EMA crossover on THAT timeframe WITH higher timeframe trend alignment

**Examples:**
- `CALL15` = 15min EMA bullish crossover, 30min is Bullish (confluence)
- `PUT15` = 15min EMA bearish crossover, 30min is Bearish (confluence)

**Generation Logic:**
- Triggered by EMA crossover on current timeframe
- Next higher timeframe EMA status **IS** aligned with crossover direction
- Tag format: `CALL{timeframe}` or `PUT{timeframe}` (e.g., `CALL15`, `PUT30`, `CALL1H`)

### 3. **Call# / Put# Signals** (Green/Red)
**Meaning:** MACD crossover (zero-line cross) on THAT timeframe WITH same timeframe EMA trend alignment

**Examples:**
- `Call30` = 30min MACD bullish crossover, 30min EMA is Bullish (confluence)
- `Put30` = 30min MACD bearish crossover, 30min EMA is Bearish (confluence)

**Generation Logic:**
- Triggered by MACD crossover on current timeframe
- Requires:
  1. Previous MACD status was opposite (BEARISH → BULLISH or BULLISH → BEARISH)
  2. Current timeframe EMA status matches MACD direction
- Tag format: `Call{timeframe}` or `Put{timeframe}` (e.g., `Call15`, `Put30`, `Call1H`)

### 4. **SQZ# Signals** (Squeeze Firing)
**Meaning:** Squeeze indicator firing on that timeframe

**Examples:**
- `SQZ15` = 15min Squeeze Firing
- `SQZ30` = 30min Squeeze Firing

**Generation Logic:**
- Triggered by "Squeeze Firing" detection in SMS
- No confluence requirements
- Tag format: `SQZ{timeframe}` (e.g., `SQZ15`, `SQZ30`, `SQZ1H`)

---

## Signal Generation Flow

### Step-by-Step Process

```
1. SMS Received → /webhook/sms endpoint
   ↓
2. parse_sms_data() - Extract:
   - Symbol (e.g., "SPY")
   - Timeframe (e.g., "15MIN")
   - Action type (macd_crossover, moving_average_crossover, squeeze_firing)
   - Direction (bullish/bearish)
   - Price (MARK value)
   ↓
3. Get Previous State (for MACD filtering)
   - Retrieve previous MACD status from state_manager
   - Retrieve current EMA status for confluence check
   ↓
4. update_system_state() - Update database
   - Record EMA or MACD crossover in timeframe_states
   - Log to state_history
   ↓
5. analyze_data() - Filter signals
   - Time filter: 5 AM - 1 PM PST/PDT only (unless bypassed)
   - Weekend filter: No alerts on Saturday/Sunday (unless bypassed)
   - MACD-specific filtering (see below)
   - EMA crossover: Always passes (no additional filtering)
   - Squeeze Firing: Always passes (no additional filtering)
   ↓
6. send_discord_alert() - Format and send
   - Determine signal tag (C#, CALL#, Call#, P#, PUT#, Put#, SQZ#)
   - Check alert toggle (per-symbol, per-tag)
   - Format Discord message
   - Send to symbol-specific webhook
```

### MACD Signal Filtering (Strict Requirements)

For **MACD crossovers** to generate `Call#` or `Put#` signals:

**Bullish Call Signal Requirements:**
1. Previous MACD status was `BEARISH` (below zero)
2. Current MACD crossover is `BULLISH` (crossing above zero)
3. Current timeframe EMA status is `BULLISH` (confluence)

**Bearish Put Signal Requirements:**
1. Previous MACD status was `BULLISH` (above zero)
2. Current MACD crossover is `BEARISH` (crossing below zero)
3. Current timeframe EMA status is `BEARISH` (confluence)

**If any requirement fails → Signal is filtered out (no alert sent)**

### EMA Signal Tag Determination

For **EMA crossovers**, the tag depends on higher timeframe confluence:

```python
# Get next higher timeframe
next_tf = state_manager.get_next_higher_timeframe(current_tf)
higher_ema_status = states[next_tf].get('ema_status')

# Determine tag
if ema_direction == 'bullish':
    tag = f"CALL{timeframe}" if higher_ema_status == 'BULLISH' else f"C{timeframe}"
else:
    tag = f"PUT{timeframe}" if higher_ema_status == 'BEARISH' else f"P{timeframe}"
```

**Example:**
- 15MIN EMA bullish crossover + 30MIN EMA is BULLISH → `CALL15`
- 15MIN EMA bullish crossover + 30MIN EMA is NOT BULLISH → `C15`
- 30MIN EMA bearish crossover + 1HR EMA is BEARISH → `PUT30`
- 30MIN EMA bearish crossover + 1HR EMA is NOT BEARISH → `P30`

---

## Signal Filtering Logic

### Time-Based Filtering

**Active Hours:** 5:00 AM - 1:00 PM PST/PDT
- Alerts outside this window are filtered (unless `ignore_time_filter=true`)
- Uses `America/Los_Angeles` timezone (handles DST automatically)

**Weekend Filter:**
- No alerts on Saturday or Sunday (unless `ignore_weekend_filter=true`)

**Bypass Options:**
- Dev mode: `alert_config.parameters["dev_mode"] = True` → bypasses both filters
- Test mode: `alert_config.parameters["ignore_time_filter"] = True` and `ignore_weekend_filter = True`

### MACD-Specific Filtering

MACD signals have strict confluence requirements (see above). If requirements aren't met, the signal is filtered.

### Alert Toggle Filtering

**Per-Symbol, Per-Tag Toggles:**
- Each symbol can have individual toggles for each tag (e.g., `SPY.C15`, `SPY.CALL15`, `SPY.Call15`)
- Toggles stored in `alert_toggles` table
- Default: All toggles enabled (`True`)
- If toggle is disabled → Alert is blocked (not sent to Discord)

**Toggle Tag Format:**
- EMA signals: `C15`, `CALL15`, `P30`, `PUT30` (uppercase)
- MACD signals: `Call15`, `Put30` (mixed case)
- Squeeze: `SQZ15`, `SQZ30` (uppercase)

---

## Discord Webhook Delivery

### Message Formatting

#### 1. MACD Signals (Call# / Put#)
```
🟢🟢 (or 🔴🔴)
15MIN MACD Cross - Call15
MARK: $450.25
TIME: 9:30 AM PST
@everyone
```

**Details:**
- Emoji count based on timeframe (1-4 emojis)
- Format: `{timeframe} MACD Cross - {Call/Put}{timeframe_suffix}`
- Toggle tag: `Call15` (mixed case)

#### 2. EMA Signals (C# / CALL# / P# / PUT#)
```
🟢🟢
15MIN EMA Cross - CALL15
MARK: $450.25
TIME: 9:30 AM PST
@everyone
```

**Details:**
- Emoji count based on timeframe
- Format: `{timeframe} EMA Cross - {tag}`
- Tag determined by higher timeframe confluence
- Toggle tag: `CALL15` or `C15` (uppercase)

#### 3. Squeeze Firing Signals
```
🔥 15 min Squeeze Firing
@everyone
```

**Details:**
- Uses original message text
- Toggle tag: `SQZ15` (uppercase)

### Webhook Selection

**Per-Symbol Webhooks:**
1. Check if dev mode enabled → use `DEV_MODE_WEBHOOK_URL`
2. Otherwise → get symbol-specific webhook from `webhook_manager.get_webhook(symbol)`
3. Fallback to default webhook if symbol not found
4. If no webhook configured → log warning and skip

**Webhook Configuration:**
- Stored in `discord_webhooks.json`
- Format:
```json
{
  "webhooks": {
    "SPY": "https://discord.com/api/webhooks/...",
    "QQQ": "https://discord.com/api/webhooks/...",
    "default": "https://discord.com/api/webhooks/..."
  }
}
```

### Delivery Process

```python
1. Format Discord message based on signal type
2. Check alert toggle (per-symbol, per-tag)
   - If disabled → return early (no send)
3. Create payload: {"content": message}
4. POST to webhook URL
5. Log success (204) or error
```

**Error Handling:**
- 204 status = Success
- 404 = Webhook deleted/invalid
- 401 = Unauthorized
- 400 = Bad request format
- All errors logged with details

---

## State Management

### Database Schema

#### `timeframe_states` Table
Tracks current EMA and MACD status per symbol/timeframe:

```sql
CREATE TABLE timeframe_states (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    ema_status TEXT DEFAULT 'UNKNOWN',  -- BULLISH, BEARISH, UNKNOWN
    macd_status TEXT DEFAULT 'UNKNOWN',  -- BULLISH, BEARISH, UNKNOWN
    last_ema_update TIMESTAMP,
    last_macd_update TIMESTAMP,
    last_ema_price REAL,
    last_macd_price REAL,
    UNIQUE(symbol, timeframe)
)
```

#### `state_history` Table
Historical record of all crossover events:

```sql
CREATE TABLE state_history (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    crossover_type TEXT NOT NULL,  -- 'ema' or 'macd'
    old_status TEXT,                -- Previous status
    new_status TEXT NOT NULL,       -- New status
    price REAL,
    timestamp TIMESTAMP
)
```

### State Update Logic

**When EMA Crossover Detected:**
1. Get current state for symbol/timeframe
2. Check if EMA status changed
3. If changed → Update `ema_status` and `last_ema_update`
4. Log to `state_history` with old/new status

**When MACD Crossover Detected:**
1. Get current state for symbol/timeframe
2. Check if MACD status changed
3. If changed → Update `macd_status` and `last_macd_update`
4. Log to `state_history` with old/new status

**Bootstrap from History:**
- On startup, `bootstrap_from_history()` rebuilds current states from latest history entries
- Ensures state persistence across restarts

---

## Alert Toggle System

### Purpose
Fine-grained control over which alert types are sent per symbol.

### Toggle Structure

**Per-Symbol, Per-Tag:**
- Each symbol (SPY, QQQ, etc.) has independent toggles
- Each tag (C15, CALL15, Call15, P30, PUT30, Put30, SQZ15, etc.) can be enabled/disabled

**Default Behavior:**
- All toggles default to `enabled = True`
- If toggle doesn't exist → defaults to enabled

### Toggle Tags

**EMA Signals:**
- `C{timeframe}` - Basic EMA bullish (e.g., `C15`, `C30`, `C1H`)
- `CALL{timeframe}` - EMA bullish with confluence (e.g., `CALL15`, `CALL30`)
- `P{timeframe}` - Basic EMA bearish (e.g., `P15`, `P30`)
- `PUT{timeframe}` - EMA bearish with confluence (e.g., `PUT15`, `PUT30`)

**MACD Signals:**
- `Call{timeframe}` - MACD bullish with EMA confluence (e.g., `Call15`, `Call30`)
- `Put{timeframe}` - MACD bearish with EMA confluence (e.g., `Put15`, `Put30`)

**Squeeze Signals:**
- `SQZ{timeframe}` - Squeeze firing (e.g., `SQZ15`, `SQZ30`)

### Toggle Check Flow

```python
# In send_discord_alert()
toggle_tag = determine_tag_from_signal()  # e.g., "CALL15", "Call30", "SQZ15"
is_enabled = alert_toggle_manager.is_enabled(symbol, toggle_tag)

if not is_enabled:
    logger.info(f"ALERT BLOCKED by toggle: {symbol} {toggle_tag}")
    return  # Don't send alert
```

### Management Interface

**API Endpoints:**
- `GET /alerts/{symbol}` - Get all toggles for symbol
- `POST /alerts/{symbol}` - Set multiple toggles at once
- `GET /admin/alerts` - HTML interface for managing toggles

**Example Toggle Update:**
```json
POST /alerts/SPY
{
  "C15": true,
  "CALL15": false,
  "Call15": true,
  "P30": true,
  "PUT30": false,
  "Put30": true,
  "SQZ15": true
}
```

---

## Key Code Locations

### Signal Parsing
- **File:** `main.py`
- **Function:** `parse_sms_data()` (lines 677-919)
- **Detects:** EMA crossovers, MACD crossovers, Squeeze Firing

### Signal Filtering
- **File:** `main.py`
- **Function:** `analyze_data()` (lines 983-1112)
- **Filters:** Time window, weekend, MACD confluence requirements

### State Updates
- **File:** `main.py`
- **Function:** `update_system_state()` (lines 921-981)
- **Calls:** `state_manager.update_timeframe_state()`

### Discord Message Formatting
- **File:** `main.py`
- **Function:** `send_discord_alert()` (lines 1114-1366)
- **Determines:** Signal tags (C#, CALL#, Call#, P#, PUT#, Put#, SQZ#)
- **Sends:** Formatted message to Discord webhook

### State Management
- **File:** `state_manager.py`
- **Class:** `StateManager`
- **Key Methods:**
  - `update_timeframe_state()` - Update EMA/MACD status
  - `get_timeframe_state()` - Get current state
  - `get_all_states()` - Get all timeframes for symbol
  - `get_next_higher_timeframe()` - Get confluence timeframe

### Alert Toggles
- **File:** `alert_toggle_manager.py`
- **Class:** `AlertToggleManager`
- **Key Methods:**
  - `is_enabled()` - Check if tag is enabled
  - `set_many()` - Update multiple toggles
  - `get()` - Get all toggles for symbol

---

## Summary

### Signal Generation Summary

1. **C# / P#**: EMA crossover without higher TF confluence
2. **CALL# / PUT#**: EMA crossover with higher TF confluence
3. **Call# / Put#**: MACD crossover with same TF EMA confluence (strict filtering)
4. **SQZ#**: Squeeze Firing (no filtering)

### Filtering Summary

1. **Time Filter**: 5 AM - 1 PM PST/PDT (bypassable)
2. **Weekend Filter**: No alerts Sat/Sun (bypassable)
3. **MACD Filter**: Requires previous status change + EMA confluence
4. **Toggle Filter**: Per-symbol, per-tag enable/disable

### Delivery Summary

1. Format message based on signal type
2. Check alert toggle
3. Select webhook (symbol-specific or default)
4. POST to Discord webhook
5. Log result

---

## Additional Notes

- **Price Alerts**: Separate system for price-level alerts (bypasses time/weekend filters)
- **Daily EMA Summaries**: Posted at 6:30 AM PT to each symbol's webhook
- **Dev Mode**: Uses separate webhook and bypasses filters
- **State Persistence**: All states survive restarts via SQLite database
- **Multi-Symbol Support**: Each symbol can have its own webhook and toggle settings


