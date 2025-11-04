# Discord Bot Setup Guide

This guide will help you set up Discord slash commands for your Trade Alerts app.

## Prerequisites

✅ You've already completed:
- Created Discord application
- Created bot and got token
- Added bot to server

## Step 1: Get Your Bot Public Key

1. Go to https://discord.com/developers/applications
2. Select your application
3. Go to **General Information** section
4. Copy the **Public Key** (this is what you need for `DISCORD_BOT_PUBLIC_KEY`)

## Step 2: Set Environment Variables

Set these environment variables in your deployment platform (Railway, Heroku, etc.):

```bash
DISCORD_BOT_PUBLIC_KEY=your-public-key-here
DISCORD_BOT_TOKEN=your-bot-token-here
```

**Note:** The bot token is sensitive - keep it secure!

## Step 3: Register Slash Commands

Run the registration script once to register commands with Discord:

```bash
# Set environment variables
export DISCORD_BOT_TOKEN="your-token-here"
export DISCORD_APPLICATION_ID="1435383006524080158"  # From your OAuth URL

# Or set OAuth URL and we'll extract the ID
export DISCORD_OAUTH_URL="https://discord.com/oauth2/authorize?client_id=1435383006524080158&permissions=2147485696&integration_type=0&scope=applications.commands+bot"

# Register commands
python register_discord_commands.py
```

**Note:** Global commands can take up to 1 hour to appear in Discord. For immediate testing, you can register guild-specific commands (run once per server).

## Step 4: Configure Interaction URL

1. Go to https://discord.com/developers/applications
2. Select your application
3. Go to **General Information** → **Interactions Endpoint URL**
4. Set it to: `https://your-app-url.railway.app/discord/interactions`
5. Click **Save Changes**

Discord will verify the endpoint (PING request). Make sure your server is running!

## Step 5: Test Commands

In your Discord server, try:
- `/dev-mode enabled:true` - Enable dev mode
- `/dev-mode enabled:false` - Disable dev mode
- `/test-mode` - Enable test mode
- `/status` - Show system status

## Available Commands

### `/dev-mode enabled:<boolean>`
Enable or disable dev mode. When enabled:
- Uses dev webhook instead of production webhooks
- Bypasses time/weekend filters

### `/test-mode`
Enable test mode (bypasses time/weekend filters for testing)

### `/status`
Show current system status (dev mode, filters, etc.)

## Troubleshooting

### Commands not appearing?
- Global commands can take up to 1 hour to propagate
- Make sure you ran `register_discord_commands.py`
- Check that bot has `applications.commands` scope

### "Interaction failed" error?
- Check that your server is running
- Verify interaction URL is correct in Discord Developer Portal
- Check logs for signature verification errors
- Make sure `DISCORD_BOT_PUBLIC_KEY` is set correctly

### Signature verification failing?
- Verify `DISCORD_BOT_PUBLIC_KEY` matches the one in Developer Portal
- Make sure the public key is the full hex string (no spaces, no dashes)

## Security Notes

- ✅ Signature verification is automatic when `DISCORD_BOT_PUBLIC_KEY` is set
- ✅ Only requests with valid signatures are processed
- ✅ Bot token should be kept secret (never commit to git)
- ✅ Public key is safe to commit (it's public by design)

