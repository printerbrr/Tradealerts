# Alternative Channel Setup Guide

## Overview

The **Alternative Channel** is a separate Discord channel system that uses **different signal rules and formatting** than the main production channel. It operates completely independently and does not affect existing functionality.

### Key Features

- ✅ **Independent Rules**: Custom filtering logic separate from main channel
- ✅ **Different Formatting**: Custom message format for alternative channel
- ✅ **Same State Tracking**: Uses the same EMA/MACD state tracking system
- ✅ **Non-Intrusive**: Does not modify or affect existing production channels
- ✅ **Optional**: Only active when webhook is configured

---

## Setup Instructions

### 1. Create Discord Webhook

1. Go to your Discord server
2. Right-click on the channel where you want alternative signals
3. Go to **Edit Channel** → **Integrations** → **Webhooks**
4. Click **New Webhook** or **Create Webhook**
5. Copy the webhook URL

### 2. Configure Alternative Channel Webhook

**Option A: Via API Endpoint**

```bash
POST /config/alternative-channel-webhook
Content-Type: application/json

{
  "webhook_url": "https://discord.com/api/webhooks/YOUR_WEBHOOK_URL"
}
```

**Option B: Via Environment Variable**

Add to your `.env` file:
```
ALTERNATIVE_CHANNEL_WEBHOOK_URL=https://discord.com/api/webhooks/YOUR_WEBHOOK_URL
```

**Option C: Via Config File**

Create `alternative_channel_webhook.txt` in the project root:
```
https://discord.com/api/webhooks/YOUR_WEBHOOK_URL
```

### 3. Verify Configuration

```bash
GET /config/alternative-channel-webhook
```

Response:
```json
{
  "configured": true,
  "webhook_preview": "https://discord.com/api/webhooks/..."
}
```

---

## Default Rules (Customizable)

The alternative channel currently implements these rules (you can modify them in `alternative_channel.py`):

### Signal Filtering

1. **EMA Crossovers**: ✅ Allowed (no time filter, no weekend filter)
2. **MACD Crossovers**: ✅ Allowed if EMA confluence exists (less strict than main channel)
3. **Squeeze Firing**: ✅ Allowed

### Differences from Main Channel

| Feature | Main Channel | Alternative Channel |
|---------|-------------|-------------------|
| Time Filter | 5 AM - 1 PM PST | ❌ No time filter |
| Weekend Filter | No alerts Sat/Sun | ❌ No weekend filter |
| MACD Requirements | Previous status change + EMA confluence | ✅ Just EMA confluence |
| Alert Toggles | Per-symbol, per-tag | ❌ No toggle system |

---

## Customizing Rules

Edit `alternative_channel.py` to customize the rules:

### Modify Filtering Logic

Edit the `analyze_alternative_channel()` function:

```python
def analyze_alternative_channel(parsed_data: Dict[str, Any]) -> bool:
    """
    Modify this function to implement your custom filtering rules
    """
    action = parsed_data.get('action')
    
    # Example: Only allow EMA crossovers on 15MIN or higher
    if action == 'moving_average_crossover':
        timeframe = parsed_data.get('timeframe', '').upper()
        if timeframe in ['15MIN', '30MIN', '1HR', '2HR', '4HR', '1DAY']:
            return True
        return False
    
    # Add your custom rules here
    return False
```

### Modify Message Format

Edit the `format_alternative_channel_message()` function:

```python
def format_alternative_channel_message(parsed_data: Dict[str, Any], log_data: Dict[str, Any]) -> Optional[str]:
    """
    Modify this function to customize the message format
    """
    # Your custom formatting here
    message = f"""Your custom format here"""
    return message
```

---

## Message Format Examples

### Current Alternative Channel Format

**MACD Signal:**
```
🟢 **MACD BULLISH**
**SPY** 15MIN
Price: $450.25
Time: 9:30 AM PST
@everyone
```

**EMA Signal:**
```
🟢 **EMA BULLISH (Higher TF Aligned)**
**SPY** 15MIN
Price: $450.25
Time: 9:30 AM PST
@everyone
```

**Squeeze Firing:**
```
🔥 **SQUEEZE FIRING**
**SPY** 15MIN
Time: 9:30 AM PST
@everyone
```

---

## How It Works

### Signal Flow

```
1. SMS Received → /webhook/sms
   ↓
2. Parse Signal (same as main channel)
   ↓
3. Update State (same as main channel)
   ↓
4. Main Channel Analysis → Send to main channel (if rules pass)
   ↓
5. Alternative Channel Analysis → Send to alternative channel (if rules pass)
   ↓
   (Both channels operate independently)
```

### Key Points

- **Same State Tracking**: Both channels use the same `state_manager` database
- **Independent Filtering**: Each channel has its own filtering logic
- **Independent Formatting**: Each channel has its own message format
- **Non-Blocking**: If alternative channel fails, main channel still works
- **Optional**: Alternative channel only sends if webhook is configured

---

## API Endpoints

### Get Alternative Channel Configuration
```http
GET /config/alternative-channel-webhook
```

### Set Alternative Channel Webhook
```http
POST /config/alternative-channel-webhook
Content-Type: application/json

{
  "webhook_url": "https://discord.com/api/webhooks/..."
}
```

---

## Troubleshooting

### Alternative Channel Not Sending

1. **Check if webhook is configured:**
   ```bash
   GET /config/alternative-channel-webhook
   ```

2. **Check logs for filtering:**
   - Look for "ALTERNATIVE CHANNEL: Signal filtered" messages
   - Signals are filtered if `analyze_alternative_channel()` returns `False`

3. **Check logs for errors:**
   - Look for "Error sending to alternative channel" messages
   - Errors are logged but don't affect main channel

### Testing

1. Send a test SMS signal
2. Check main channel (should work as before)
3. Check alternative channel (should work if rules pass)
4. Check logs for both channels

---

## File Structure

```
TradeAlerts/
├── alternative_channel.py          # Alternative channel logic
├── main.py                          # Main application (imports alternative_channel)
├── alternative_channel_webhook.txt  # Webhook config file (auto-created)
└── ALTERNATIVE_CHANNEL_SETUP.md     # This file
```

---

## Customization Examples

### Example 1: Only Higher Timeframes

```python
def analyze_alternative_channel(parsed_data: Dict[str, Any]) -> bool:
    timeframe = parsed_data.get('timeframe', '').upper()
    # Only allow 1HR and higher
    if timeframe in ['1HR', '2HR', '4HR', '1DAY']:
        return True
    return False
```

### Example 2: Only MACD Signals

```python
def analyze_alternative_channel(parsed_data: Dict[str, Any]) -> bool:
    action = parsed_data.get('action')
    # Only allow MACD crossovers
    if action == 'macd_crossover':
        return True
    return False
```

### Example 3: Custom Time Filter

```python
def analyze_alternative_channel(parsed_data: Dict[str, Any]) -> bool:
    # Only allow signals between 8 AM - 12 PM PST
    pacific = pytz.timezone('America/Los_Angeles')
    current_time = datetime.now(pacific)
    hour = current_time.hour
    
    if 8 <= hour < 12:
        # Your signal logic here
        return True
    return False
```

---

## Notes

- The alternative channel is **completely optional** - if not configured, it simply doesn't send
- All existing functionality remains **unchanged**
- The alternative channel uses the **same state tracking** as the main channel
- You can have **multiple alternative channels** by creating additional modules (future enhancement)

---

## Support

For issues or questions:
1. Check the logs: `trade_alerts.log`
2. Verify webhook configuration
3. Test with a simple signal first
4. Modify `alternative_channel.py` to customize rules


