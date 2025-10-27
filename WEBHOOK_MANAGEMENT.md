# Webhook Management Guide

## Overview

The system now supports **multi-ticker webhook routing** - each ticker can have its own Discord channel, with a fallback for unmapped tickers.

## Backward Compatibility

✅ **SPY continues to work exactly as before** - your existing webhook is already configured.

## Adding New Tickers via API

### Setup Steps:

1. **Go to your Railway deployment**: `https://your-app.railway.app/docs`

2. **Add a new ticker webhook**:
   - Endpoint: `POST /webhooks/{symbol}`
   - Example: `POST /webhooks/QQQ`
   - Body:
     ```json
     {
       "webhook_url": "https://discord.com/api/webhooks/YOUR_WEBHOOK_URL"
     }
     ```

### Available Endpoints:

#### View All Webhooks
- **GET** `/webhooks`
- Returns all configured webhooks (URLs are masked for security)

#### View Specific Webhook
- **GET** `/webhooks/{symbol}`
- Example: `GET /webhooks/SPY`
- Returns webhook status for that symbol

#### Add/Update Webhook
- **POST** `/webhooks/{symbol}`
- Example: `POST /webhooks/QQQ`
- Body: `{"webhook_url": "https://discord.com/..."}`
- Adds new ticker or updates existing one

#### Remove Webhook
- **DELETE** `/webhooks/{symbol}`
- Example: `DELETE /webhooks/QQQ`
- Removes webhook for that symbol (keeps default fallback)

#### List All Tracked Symbols
- **GET** `/symbols`
- Returns list of all configured symbols

## Example Workflow

### Adding QQQ as a New Ticker:

1. **Create a Discord webhook**:
   - Go to your Discord server
   - Create a new channel (e.g., `#qqq-alerts`)
   - Right-click channel → Integrations → Webhooks
   - Create webhook → Copy URL

2. **Configure in system**:
   - Open: `https://your-app.railway.app/docs`
   - Find: `POST /webhooks/{symbol}`
   - Symbol: `QQQ`
   - Request body:
     ```json
     {
       "webhook_url": "https://discord.com/api/webhooks/YOUR_URL"
     }
     ```
   - Execute

3. **Verify**:
   - Test with: `GET /webhooks/QQQ`
   - Should show webhook is configured

4. **Done!** 
   - Alerts for QQQ will now go to that channel
   - No code changes needed

## How It Works

### Routing Logic:
```
Alert arrives with symbol (e.g., "QQQ")
  ↓
System checks discord_webhooks.json
  ↓
Has "QQQ" webhook? → Use it
  ↓
No? Check "default" webhook → Use it
  ↓
No default? Log warning
```

### Database Storage:
- Each symbol has its own state tracking
- Database stores: `(symbol, timeframe, EMA_status, MACD_status)`
- SPY, QQQ, DIA, etc. are stored separately

### Confluence Rules:
- Rules are symbol-agnostic
- Same rules apply to all tickers
- Check per-ticker state before sending alerts

## File Structure

```
TradeAlerts/
├── discord_webhooks.json    # Webhook configuration (auto-created)
├── discord_config.txt       # Legacy config (still works)
├── webhook_manager.py       # Webhook routing logic
├── main.py                  # Updated with webhook routing
└── state_manager.py         # Per-symbol state tracking (unchanged)
```

## Security Notes

- `discord_webhooks.json` is in `.gitignore` (won't be committed)
- Full webhook URLs are not exposed in GET requests (masked)
- Default webhook protects against unmapped tickers

## Troubleshooting

### Alerts Not Sending?
1. Check webhook configuration: `GET /webhooks/{symbol}`
2. Verify webhook URL is valid
3. Check logs for errors

### Adding 5-6 Tickers?
1. Create 5-6 Discord channels (one per ticker)
2. Create webhook for each channel
3. Use `POST /webhooks/{symbol}` for each
4. No limits - add as many as needed

### Want Same Channel for Multiple Tickers?
- Just use the same webhook URL for multiple symbols
- Example: `POST /webhooks/QQQ` and `POST /webhooks/DIA` with same URL

## Current Status

✅ SPY: Configured and working  
⏳ New tickers: Waiting for webhook configuration via API

