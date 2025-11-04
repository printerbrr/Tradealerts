# Discord Bot Usage Guide - Post Setup

This guide covers everything you need to know **after** your Discord bot is set up and the server is live.

## Step 1: Add Bot to Your Discord Server (If Not Already Done)

1. Use your OAuth URL:
   ```
   https://discord.com/oauth2/authorize?client_id=1435383006524080158&permissions=2147485696&integration_type=0&scope=applications.commands+bot
   ```
2. Open the URL in your browser
3. Select your Discord server from the dropdown
4. Click **Authorize** (the bot will ask for permissions)
5. You should see the bot appear in your server's member list

**Note:** If you've already added the bot, skip to Step 2.

---

## Step 2: Register Slash Commands

Run the registration script **once** to register commands with Discord:

```bash
# Make sure you're in the TradeAlerts directory
cd TradeAlerts

# The script will read from your .env file automatically
python register_discord_commands.py
```

**Expected output:**
```
📡 Registering 3 slash commands...
   Application ID: 1435383006524080158
   URL: https://discord.com/api/v10/applications/1435383006524080158/commands

✅ Successfully registered commands:
   • /dev-mode - Enable or disable dev mode (uses dev webhook, bypasses filters)
   • /test-mode - Enable test mode (bypasses time/weekend filters for testing)
   • /status - Show current system status (dev mode, filters, etc.)

💡 Commands are now available in your Discord server!
   Note: Global commands can take up to 1 hour to appear
   For immediate testing, use guild commands (register to specific server)
```

**Important:** Global commands can take up to 1 hour to appear in Discord. For immediate testing, you can register to a specific server (guild).

---

## Step 3: Where to Use Slash Commands

1. Open your Discord server
2. Navigate to **any channel** where you have permission to send messages
3. Type `/` in the message input box
4. You should see your bot's commands appear in the autocomplete menu

**Where commands work:**
- ✅ Any text channel you can type in
- ✅ Direct messages (if bot is in DMs)
- ✅ Server channels where bot has access

---

## Step 4: Test Commands

### Command 1: Check System Status

**Type in Discord:**
```
/status
```

**Expected response:**
```
System Status:
• Dev Mode: 🔴 OFF
• Time Filter: 🟢 ON (5am-1pm PT)
• Weekend Filter: 🟢 ON
```

**What it does:** Shows current system configuration

---

### Command 2: Enable Dev Mode

**Type in Discord:**
```
/dev-mode enabled:true
```

**Expected response:**
```
Dev mode enabled ✅
- Using dev webhook
- Time/weekend filters bypassed
```

**What it does:**
- ✅ Switches to dev webhook (if configured)
- ✅ Bypasses time filters (5am-1pm)
- ✅ Bypasses weekend filters
- ✅ Perfect for testing outside market hours

---

### Command 3: Disable Dev Mode

**Type in Discord:**
```
/dev-mode enabled:false
```

**Expected response:**
```
Dev mode disabled ✅
- Using production webhooks
- Normal filters active
```

**What it does:** Returns to production mode with normal filters

---

### Command 4: Enable Test Mode

**Type in Discord:**
```
/test-mode
```

**Expected response:**
```
Test mode enabled ✅
- Time/weekend filters disabled
```

**What it does:**
- ✅ Bypasses time/weekend filters
- ✅ Keeps production webhooks
- ✅ Useful for quick testing

---

## Step 5: Test Workflow Example

Here's a complete test workflow:

1. **Check current status:**
   ```
   /status
   ```
   Should show filters are ON

2. **Enable dev mode:**
   ```
   /dev-mode enabled:true
   ```
   Confirms dev mode is enabled

3. **Send a test alert** (via your SMS webhook or API)
   - Should use dev webhook
   - Should bypass time/weekend filters
   - Should appear in your dev Discord channel

4. **Verify status changed:**
   ```
   /status
   ```
   Should show Dev Mode: 🟢 ON

5. **Disable dev mode:**
   ```
   /dev-mode enabled:false
   ```
   Returns to normal operation

---

## Troubleshooting

### Commands Don't Appear?

- ⏰ **Wait up to 1 hour** for global commands to propagate
- ✅ Make sure you ran `register_discord_commands.py`
- ✅ Verify the bot is in your server
- 🔄 Try restarting Discord

### "Interaction Failed" Error?

- 📋 Check Railway logs for errors
- ✅ Verify interaction URL is set correctly in Discord Developer Portal
- ✅ Make sure environment variables are set in Railway
- ✅ Check that bot token is correct

### Command Works But No Response?

- 📋 Check Railway logs for command execution
- ✅ Verify bot has permission to send messages in that channel
- 🔍 Check for any errors in the logs

---

## Quick Reference

| Command | What It Does | When To Use |
|---------|-------------|-------------|
| `/status` | Show current system status | Check configuration anytime |
| `/dev-mode enabled:true` | Enable dev mode | Testing with dev webhook |
| `/dev-mode enabled:false` | Disable dev mode | Return to production |
| `/test-mode` | Enable test mode | Quick testing (bypass filters) |

---

## Tips

1. **Commands are case-sensitive** - Use exactly `/dev-mode` not `/Dev-Mode`
2. **Use autocomplete** - Type `/` and select from the menu
3. **Check logs** - Railway logs show when commands are received
4. **Test in private channel first** - Verify everything works before using in production channels

---

## Next Steps

1. ✅ Test all commands to ensure they work
2. ✅ Set up your dev webhook URL (if you haven't already)
3. ✅ Test sending alerts while dev mode is enabled
4. ✅ Verify alerts go to the correct Discord channel

You're all set! Start with `/status` to confirm everything is working.

