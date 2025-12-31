# Trade Alerts System - Comprehensive Codebase Audit

## Executive Summary

This system is a **SMS-based trade alerting system** that:
1. Receives SMS messages from your phone via Tasker (Android automation app)
2. Parses trading signals (EMA crossovers, MACD crossovers, Squeeze Firing)
3. Tracks market state across multiple timeframes (1MIN, 5MIN, 15MIN, 30MIN, 1HR, 2HR, 4HR, 1DAY)
4. Applies confluence rules to filter alerts
5. Sends formatted alerts to Discord channels

---

## 1. Message Flow: Phone → Server → Discord

### 1.1 Phone to Server (SMS Reception)

**Path:** Android Phone → Tasker → HTTP POST → FastAPI Server

**How it works:**
1. **Tasker Setup** (Android automation app):
   - Profile: Event → Phone → Received Text
   - Task: HTTP Request action
   - **Endpoint:** `POST /webhook/sms`
   - **Payload:**
     ```json
     {
       "sender": "%SMSRF",      // SMS sender number
       "message": "%SMSRB",      // SMS message body
       "timestamp": "%DATE %TIME" // Optional timestamp
     }
     ```

2. **Server Reception** (`main.py:445-683`):
   - Endpoint: `POST /webhook/sms`
   - Handles malformed JSON (escapes control characters)
   - Extracts `sender` and `message` from request body
   - Logs raw request for debugging

**Key Code Location:** `main.py:445` - `receive_sms()` function

### 1.2 Message Parsing

**Function:** `parse_sms_data(message: str)` in `main.py:685-927`

**Supported Message Types:**

#### A. Schwab Alerts (Primary Format)
**Detection:** Contains "schwab" or "alert on" in message

**Extracted Fields:**
- **Symbol:** Extracted from `ALERT ON {SYMBOL}` pattern
- **Price:** Extracted from `MARK = {price}` pattern
- **Timeframe:** Extracted from patterns like `5MIN TF`, `1HR SQUEEZE`, etc.
  - Normalized formats: `1MIN`, `5MIN`, `15MIN`, `30MIN`, `1HR`, `2HR`, `4HR`, `1DAY`
- **EMA Pair:** Extracted from `TF {code}` pattern (e.g., `TF 921` = 9/21 EMAs)
- **Action Type:** Determined by keywords:
  - `macd_crossover`: "macdhistogramcrossover", "macd crossover", "macd cross"
  - `moving_average_crossover`: "movingavgcrossover", "crossover", "ema cross"
  - `squeeze_firing`: "squeeze firing"
- **Direction:** 
  - MACD: "negative to positive" → `bullish`, "positive to negative" → `bearish`
  - EMA: "negative to positive" → `bullish`, "positive to negative" → `bearish`
- **Study Details:** Extracted from `STUDY = {value}` pattern
- **Trigger Time:** Extracted from `SUBMIT AT {date time}` pattern

#### B. Squeeze Firing Signals
**Detection:** Contains "squeeze firing" in message

**Extracted Fields:**
- **Timeframe:** From patterns like "15 min Squeeze Firing", "15MIN Squeeze"
- **Symbol:** Optional, extracted from message or defaults to "SPY"

#### C. Price Alerts (Special Handling)
**Detection:** Contains "mark is at or above" or "mark is at or below"

**Special Path:** Bypasses normal alert flow, goes directly to price alert handler
- **Function:** `parse_price_alert()` in `main.py:1999-2055`
- **Extracted:** Symbol, direction (AT OR ABOVE/BELOW), alert level, current mark
- **Destination:** Separate Discord webhook (`PRICE_ALERT` webhook)

**Key Code Locations:**
- `main.py:685` - `parse_sms_data()` - Main parser
- `main.py:1999` - `parse_price_alert()` - Price alert parser

---

## 2. State Management System

### 2.1 Database Structure

**Database:** SQLite (`market_states.db`)

**Tables:**

1. **`timeframe_states`** - Current state per symbol/timeframe
   - `symbol` (TEXT)
   - `timeframe` (TEXT): 1MIN, 5MIN, 15MIN, 30MIN, 1HR, 2HR, 4HR, 1DAY
   - `ema_status` (TEXT): BULLISH, BEARISH, UNKNOWN
   - `macd_status` (TEXT): BULLISH, BEARISH, UNKNOWN
   - `last_ema_update` (TIMESTAMP)
   - `last_macd_update` (TIMESTAMP)
   - `last_ema_price` (REAL)
   - `last_macd_price` (REAL)

2. **`state_history`** - Historical crossover events
   - `symbol`, `timeframe`, `crossover_type` (ema/macd)
   - `old_status`, `new_status`, `price`, `timestamp`

3. **`alert_toggles`** - Per-symbol alert tag enable/disable
   - `symbol`, `tag` (e.g., "C1", "CALL5", "Put15"), `enabled` (0/1)

4. **`system_metadata`** - System-level tracking
   - `key`, `value` (e.g., "last_daily_summary_date")

**Key Code Location:** `state_manager.py`

### 2.2 State Update Flow

**Function:** `update_system_state(parsed_data)` in `main.py:929-989`

**Process:**
1. Extract symbol, timeframe, action, direction from parsed data
2. Get current state from database
3. **Only update if status changed** (prevents duplicate updates)
4. Update database:
   - For MACD crossovers: Update `macd_status`, `last_macd_update`, `last_macd_price`
   - For EMA crossovers: Update `ema_status`, `last_ema_update`, `last_ema_price`
5. Log change to `state_history` table

**Key Code Location:** `main.py:929` - `update_system_state()`

---

## 3. Alert Analysis & Filtering

### 3.1 Main Alert Analysis

**Function:** `analyze_data(parsed_data)` in `main.py:991-1120`

**Filtering Layers:**

#### Layer 1: Time & Weekend Filters
- **Time Filter:** Only allows alerts between 5 AM - 1 PM PST/PDT
  - Configurable via `ignore_time_filter` parameter
- **Weekend Filter:** Blocks alerts on Saturday/Sunday
  - Configurable via `ignore_weekend_filter` parameter

#### Layer 2: Alert Type Filtering

**A. MACD Crossovers** (`action == "macd_crossover"`)
**Conditions for Bullish "Call" Signal:**
1. Previous MACD status was `BEARISH` (below 0)
2. Current MACD direction is `BULLISH` (crossing above 0)
3. Current timeframe EMA status is `BULLISH` (confluence)

**Conditions for Bearish "Put" Signal:**
1. Previous MACD status was `BULLISH` (above 0)
2. Current MACD direction is `BEARISH` (crossing below 0)
3. Current timeframe EMA status is `BEARISH` (confluence)

**B. EMA Crossovers** (`action == "moving_average_crossover"`)
- **Always triggers alert** (no additional confluence requirements)
- Confluence with next higher timeframe is checked during message formatting

**C. Squeeze Firing** (`action == "squeeze_firing"`)
- **Always triggers alert**

**D. All Other Alerts**
- **Filtered out** (not categorized into known types)

**Key Code Location:** `main.py:991` - `analyze_data()`

### 3.2 Confluence Rules System

**Purpose:** Configurable rules engine for filtering alerts based on timeframe confluence

**Configuration File:** `confluence_rules.json`

**Current Rules:**

1. **"MACD confluence with next higher EMA"** (ENABLED)
   - **Trigger:** Any MACD crossover on any timeframe
   - **Requirement:** Next higher timeframe EMA must be in same direction
   - **Action:** ALLOW (if requirement met)

2. **"EMA confluence with next higher EMA"** (DISABLED)
   - **Trigger:** Any EMA crossover on any timeframe
   - **Requirement:** Next higher timeframe EMA must be in same direction
   - **Action:** ALLOW (if requirement met)

3. **"Block alerts without confluence"** (DISABLED)
   - **Trigger:** Any alert
   - **Requirement:** None
   - **Action:** BLOCK

**How It Works:**
1. Rules are loaded from `confluence_rules.json` on startup
2. When an alert is evaluated, `confluence_rules.get_applicable_rules()` finds matching rules
3. For each applicable rule, `confluence_rules.evaluate_alert()` checks requirements
4. Requirements check state from `state_manager` (e.g., next higher timeframe EMA status)
5. If requirements pass, rule's action (ALLOW/BLOCK) is applied

**Note:** Currently, confluence rules are **NOT actively used** in the main alert flow. The MACD confluence check is hardcoded in `analyze_data()`, and EMA confluence is checked during message formatting.

**Key Code Location:** `confluence_rules.py`

---

## 4. Discord Message Generation

### 4.1 Main Channel Messages

**Function:** `send_discord_alert(log_data)` in `main.py:1122-1374`

**Message Formats:**

#### A. MACD Crossover Messages
**Format:**
```
{emoji_str}
{timeframe} MACD Cross - {Call/Put}{suffix}
MARK: ${price}
TIME: {time}
@everyone
```

**Example:**
```
🟢🟢
5MIN MACD Cross - Call5
MARK: $450.25
TIME: 9:30 AM PST
@everyone
```

**Details:**
- **Emoji Count:** Based on timeframe (5MIN = 2 emojis, 15MIN/30MIN = 2, 1HR/2HR = 3, 4HR/1DAY = 4)
- **Direction Label:** "Call" (bullish) or "Put" (bearish) - **mixed case**
- **Suffix:** Timeframe token (e.g., "5" for 5MIN, "1H" for 1HR, "1D" for 1DAY)
- **Toggle Tag:** `Call{suffix}` or `Put{suffix}` (e.g., "Call5", "Put15")

#### B. EMA Crossover Messages
**Format:**
```
{emoji_str}
{timeframe} EMA Cross - {tag}
MARK: ${price}
TIME: {time}
@everyone
```

**Example:**
```
🟢🟢🟢
1HR EMA Cross - CALL1H
MARK: $450.25
TIME: 9:30 AM PST
@everyone
```

**Details:**
- **Tag Logic:** Based on next higher timeframe EMA confluence
  - If bullish + next higher EMA is BULLISH → `CALL{suffix}` (uppercase)
  - If bullish + next higher EMA is NOT BULLISH → `C{suffix}` (single letter)
  - If bearish + next higher EMA is BEARISH → `PUT{suffix}` (uppercase)
  - If bearish + next higher EMA is NOT BEARISH → `P{suffix}` (single letter)
- **Toggle Tag:** Same as tag (uppercase)

#### C. Squeeze Firing Messages
**Format:**
```
🔥 {original_message}
@everyone
```

**Example:**
```
🔥 15 min Squeeze Firing
@everyone
```

**Details:**
- Uses original message text exactly as received
- **Toggle Tag:** `SQZ{suffix}` (e.g., "SQZ15", "SQZ1H")

**Key Code Location:** `main.py:1122` - `send_discord_alert()`

### 4.2 Alert Toggle System

**Purpose:** Per-symbol, per-tag enable/disable filtering

**How It Works:**
1. Before sending Discord message, system checks `alert_toggle_manager.is_enabled(symbol, toggle_tag)`
2. If toggle is disabled, alert is blocked (not sent)
3. Default: All toggles are **enabled** (True) if not found in database

**Toggle Tags:**
- **MACD:** `Call{suffix}`, `Put{suffix}` (mixed case)
- **EMA:** `C{suffix}`, `P{suffix}`, `CALL{suffix}`, `PUT{suffix}` (uppercase)
- **Squeeze:** `SQZ{suffix}` (uppercase)

**Key Code Location:** `main.py:1321-1327` - Toggle check before sending

### 4.3 Alternative Channel Messages

**Purpose:** Separate Discord channel with different rules (1MIN and 5MIN EMA only)

**Rules:**
1. **1MIN EMA:** Only if 5MIN EMA is in confluence (same direction)
2. **5MIN EMA:** Always send
3. **Time Filter:** 6 AM - 1 PM PST/PDT
4. **Weekend Filter:** No alerts on Saturday/Sunday

**Message Format:** Same as main channel EMA format
- Tags: `C1`, `P1`, `C5`, `P5`

**Key Code Location:** `alternative_channel.py`

### 4.4 Webhook Routing

**Function:** `webhook_manager.get_webhook(symbol)` in `webhook_manager.py:103-119`

**Process:**
1. Look for symbol-specific webhook in `discord_webhooks.json`
2. If not found, use "default" webhook
3. If no default, return None (alert not sent)

**Configuration File:** `discord_webhooks.json`
```json
{
  "webhooks": {
    "SPY": "https://discord.com/api/webhooks/...",
    "QQQ": "https://discord.com/api/webhooks/...",
    "default": "https://discord.com/api/webhooks/...",
    "PRICE_ALERT": "https://discord.com/api/webhooks/..."
  }
}
```

**Key Code Location:** `webhook_manager.py`

---

## 5. Complete Message Flow Diagram

```
┌─────────────────┐
│  Android Phone  │
│  (SMS Received) │
└────────┬────────┘
         │
         │ Tasker HTTP POST
         │ POST /webhook/sms
         │ {sender, message, timestamp}
         ▼
┌─────────────────┐
│  FastAPI Server │
│  /webhook/sms   │
└────────┬────────┘
         │
         ├─→ Parse Message (parse_sms_data)
         │   ├─→ Extract: symbol, timeframe, action, direction, price
         │   └─→ Detect: MACD/EMA/Squeeze/Price Alert
         │
         ├─→ Price Alert? ──YES──→ parse_price_alert()
         │   │                      └─→ send_price_alert_to_discord()
         │   │                          └─→ PRICE_ALERT webhook
         │   │
         │   └─NO→ Regular Alert Flow
         │
         ├─→ Get Previous State (for MACD confluence check)
         │   └─→ state_manager.get_timeframe_state()
         │
         ├─→ Update System State (update_system_state)
         │   └─→ state_manager.update_timeframe_state()
         │       ├─→ Update timeframe_states table
         │       └─→ Log to state_history table
         │
         ├─→ Analyze Alert (analyze_data)
         │   ├─→ Time/Weekend Filter Check
         │   ├─→ MACD: Check previous status + EMA confluence
         │   ├─→ EMA: Always allow
         │   └─→ Squeeze: Always allow
         │
         ├─→ Alert Triggered? ──YES──→ send_discord_alert()
         │   │                          ├─→ Format message
         │   │                          ├─→ Check alert toggle
         │   │                          ├─→ Get webhook URL
         │   │                          └─→ POST to Discord
         │   │
         │   └─NO→ Log skipped alert
         │
         └─→ Send to Alternative Channel (send_to_alternative_channel)
             ├─→ analyze_alternative_channel()
             │   └─→ Check: 1MIN/5MIN only, confluence rules
             ├─→ format_alternative_channel_message()
             └─→ POST to alternative webhook
```

---

## 6. Key Data Structures

### 6.1 Parsed Data Structure
```python
{
    "raw_message": str,
    "symbol": str,              # e.g., "SPY"
    "price": float,             # e.g., 450.25
    "action": str,              # "macd_crossover", "moving_average_crossover", "squeeze_firing"
    "timeframe": str,           # "5MIN", "1HR", etc.
    "macd_direction": str,      # "bullish" or "bearish" (for MACD)
    "ema_direction": str,       # "bullish" or "bearish" (for EMA)
    "ema_short": int,          # e.g., 9
    "ema_long": int,           # e.g., 21
    "confidence": str,         # "high", "medium", "low"
    "alert_type": str,         # "schwab_alert"
    "trigger_time": str,       # "10/22/2025 9:30:00"
    "study_details": str,      # Study value
    "_previous_macd_status": str,  # Internal: previous MACD status
    "_current_ema_status": str      # Internal: current EMA status
}
```

### 6.2 State Structure (from database)
```python
{
    "symbol": "SPY",
    "timeframe": "5MIN",
    "ema_status": "BULLISH",      # or "BEARISH", "UNKNOWN"
    "macd_status": "BEARISH",     # or "BULLISH", "UNKNOWN"
    "last_ema_update": "2025-10-22 09:30:00",
    "last_macd_update": "2025-10-22 09:25:00",
    "last_ema_price": 450.25,
    "last_macd_price": 449.80
}
```

---

## 7. Configuration Files

### 7.1 `discord_webhooks.json`
- Maps symbols to Discord webhook URLs
- Includes "default" fallback webhook
- Includes "PRICE_ALERT" webhook for price alerts

### 7.2 `confluence_rules.json`
- Configurable rules for alert filtering
- Currently: MACD confluence rule enabled, EMA confluence rule disabled

### 7.3 `alert_toggles.json` (legacy, migrated to database)
- Per-symbol alert tag toggles
- Now stored in `alert_toggles` table in database

### 7.4 Environment Variables
- `DISCORD_WEBHOOK_URL`: Legacy webhook URL
- `PRICE_ALERT_WEBHOOK_URL`: Price alert webhook
- `DEV_MODE_WEBHOOK_URL`: Dev mode webhook
- `DISCORD_BOT_PUBLIC_KEY`: Discord bot public key (for slash commands)
- `DISCORD_BOT_TOKEN`: Discord bot token

---

## 8. Additional Features

### 8.1 Daily EMA Summary
- **Schedule:** 06:30 AM PT (weekdays only)
- **Function:** `send_daily_ema_summaries()` in `main.py:1422-1475`
- **Format:** Lists EMA status for all timeframes per symbol
- **Destination:** Each symbol's webhook channel

### 8.2 Discord Slash Commands
- **Endpoint:** `POST /discord/interactions`
- **Commands:**
  - `/dev-mode`: Toggle dev mode (uses dev webhook, bypasses filters)
  - `/test-mode`: Enable test mode (bypasses time/weekend filters)
  - `/status`: Show current system status
  - `/ema-summary`: Manually trigger EMA summary

### 8.3 Admin Endpoints
- `GET /debug/states`: Inspect current timeframe states
- `GET /admin/alerts`: HTML page for managing alert toggles
- `POST /admin/send-daily-ema-summaries`: Manually trigger EMA summary

---

## 9. Summary: How Confluence Rules Translate to Discord Messages

### 9.1 MACD Alerts
1. **Confluence Check:** Hardcoded in `analyze_data()`
   - Requires: Previous MACD was opposite direction + Current EMA is same direction
2. **Message Tag:** `Call{suffix}` or `Put{suffix}` (mixed case)
3. **Example:** `Call5` = 5MIN MACD bullish crossover with 5MIN EMA bullish confluence

### 9.2 EMA Alerts
1. **Confluence Check:** During message formatting (not in analyze_data)
   - Checks: Next higher timeframe EMA status
2. **Message Tag Logic:**
   - `CALL{suffix}` = Current EMA bullish + Next higher EMA bullish (confluence)
   - `C{suffix}` = Current EMA bullish + Next higher EMA NOT bullish (no confluence)
   - `PUT{suffix}` = Current EMA bearish + Next higher EMA bearish (confluence)
   - `P{suffix}` = Current EMA bearish + Next higher EMA NOT bearish (no confluence)
3. **Example:** `CALL1H` = 1HR EMA bullish crossover with 2HR EMA bullish confluence

### 9.3 Confluence Rules Engine (Currently Unused)
- The `confluence_rules.json` system exists but is **not actively used** in the main alert flow
- MACD confluence is hardcoded, EMA confluence is checked during formatting
- The rules engine could be enabled for more flexible filtering in the future

---

## 10. Important Notes

1. **State Updates:** Only occur when status **changes** (prevents duplicate updates)
2. **Price Alerts:** Bypass all filters and go to separate webhook
3. **Alternative Channel:** Independent system with its own rules (1MIN/5MIN only)
4. **Alert Toggles:** Per-symbol, per-tag enable/disable (default: enabled)
5. **Time Filters:** 5 AM - 1 PM PST/PDT for main channel, 6 AM - 1 PM for alternative
6. **Weekend Filter:** No alerts on Saturday/Sunday (configurable)
7. **Dev Mode:** Uses separate webhook and bypasses all filters

---

## 11. File Structure Summary

```
main.py                    # Main FastAPI application, SMS endpoint, alert logic
state_manager.py           # Database operations, state tracking
webhook_manager.py         # Discord webhook URL management
confluence_rules.py        # Configurable confluence rules engine (currently unused)
alert_toggle_manager.py    # Per-symbol alert tag toggles
alternative_channel.py     # Alternative channel with different rules
discord_webhooks.json      # Webhook URL configuration
confluence_rules.json      # Confluence rules configuration
market_states.db           # SQLite database (states, history, toggles)
```

---

**End of Audit**

