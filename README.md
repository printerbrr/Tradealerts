# Trade Alerts SMS Parser

A lean SMS-based trade alerting system that receives SMS messages forwarded from your phone via Tasker, parses the data, analyzes it against configurable parameters, and sends Discord alerts when conditions are met.

## Features

- **SMS Webhook Endpoint**: Receives SMS messages forwarded from Tasker
- **Configurable Parsing**: Flexible SMS data parsing (to be configured)
- **Data Analysis**: Analyzes parsed data against your parameters
- **Discord Integration**: Sends rich alerts via Discord webhook
- **Logging**: Comprehensive logging of all SMS and alerts
- **REST API**: Easy configuration and monitoring via API endpoints

## Quick Start

1. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Run the server**:
   ```bash
   python main.py
   ```

3. **Access the API documentation**:
   - Open http://localhost:8000/docs in your browser
   - Test the SMS webhook endpoint at http://localhost:8000/webhook/sms

## Setup Instructions

### 1. Discord Webhook Setup
1. Go to your Discord server
2. Right-click on a channel → Edit Channel
3. Go to Integrations → Webhooks
4. Create a new webhook and copy the URL
5. Configure it via API: `POST /config` with your webhook URL

### 2. Tasker Setup (Android)
1. Install Tasker from Google Play Store
2. Create a new profile: Event → Phone → Received Text
3. Add a task with HTTP Request action:
   - **Method**: POST
   - **URL**: `https://your-ngrok-url.ngrok.io/webhook/sms`
   - **Headers**: `Content-Type: application/json`
   - **Body**:
     ```json
     {
       "sender": "%SMSRF",
       "message": "%SMSRB",
       "timestamp": "%DATE %TIME"
     }
     ```

### 3. Ngrok Setup (for local development)
1. Install ngrok: https://ngrok.com/download
2. Run: `ngrok http 8000`
3. Use the HTTPS URL in your Tasker configuration

## API Endpoints

- `GET /` - Health check
- `POST /webhook/sms` - Receive SMS messages
- `GET /config` - Get current configuration
- `POST /config` - Update configuration
 - `POST /admin/send-daily-ema-summaries` - Manually trigger daily EMA summaries

## Configuration

The system uses a flexible configuration system. You can update parsing rules and alert parameters via the `/config` endpoint.

### Example Configuration
```json
{
  "enabled": true,
  "discord_webhook_url": "https://discord.com/api/webhooks/...",
  "parameters": {
    "min_price": 50,
    "max_price": 1000,
    "keywords": ["buy", "sell", "alert"]
  }
}
```

## Next Steps

1. **Configure SMS Parsing**: Update the `parse_sms_data()` function based on your SMS format
2. **Set Alert Parameters**: Configure the `analyze_data()` function with your specific criteria
3. **Test the System**: Send test SMS messages to verify the flow
4. **Deploy**: Consider deploying to a cloud service for 24/7 operation

## Daily EMA Summary (06:30 PT)

The system posts a single message each morning at 06:30 AM Pacific Time to every configured ticker channel. The message lists the current EMA state for each timeframe that has data for that ticker.

Example format:

```
10/30/2025 06:30 AM
1Min - Bullish
5Min - Bearish
15Min - Bullish
...
```

Notes:
- One message per ticker channel (based on per-symbol webhooks).
- Timezone is `America/Los_Angeles` (PST/PDT handled automatically).
- Uses existing states stored in `market_states.db`.

Manual trigger (useful for testing or on-demand):
- `POST /admin/send-daily-ema-summaries`

## Logging

All SMS messages and alerts are logged to:
- Console output
- `trade_alerts.log` file

## Security Notes

- The webhook endpoint is currently open - consider adding authentication for production use
- Store sensitive configuration in environment variables
- Use HTTPS in production (ngrok provides this automatically)
