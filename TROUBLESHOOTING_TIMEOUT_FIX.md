# Tasker Timeout Fix - Troubleshooting Guide

## Problem Summary

**Issue:** Tasker was timing out after 30 seconds with error:
```
java.util.concurrent.timeoutexception: the source did not signal an event for 30000 milliseconds
```

**Root Causes:**
1. **Synchronous `requests.post()` blocking the async event loop** - Discord webhook calls were using blocking synchronous HTTP requests
2. **No timeout on Discord webhook calls** - If Discord was slow/unreachable, requests could hang indefinitely
3. **Endpoint processing everything before returning** - Tasker waited for entire processing (parsing, database updates, Discord webhooks) to complete
4. **Database operations blocking** - Synchronous SQLite operations could block the event loop

## Fixes Applied

### 1. Immediate Response to Tasker
- **Changed:** Endpoint now returns immediately after receiving SMS
- **Before:** Returned after all processing completed (could take 30+ seconds)
- **After:** Returns `{"status": "success", "message": "SMS received and processing"}` immediately
- **Processing:** All heavy work (Discord webhooks, database updates) runs in background tasks

### 2. Async HTTP Client (httpx)
- **Changed:** Replaced synchronous `requests.post()` with async `httpx.AsyncClient`
- **Benefits:**
  - Non-blocking - doesn't freeze the event loop
  - Proper async/await support
  - Better timeout handling

### 3. Timeout Protection
- **Added:** 10-second timeout on all Discord webhook calls
- **Protection:** If Discord is slow/unreachable, request fails after 10 seconds instead of hanging
- **Error Handling:** Proper timeout exceptions with logging

### 4. Background Task Processing
- **Changed:** Discord webhook calls run in `asyncio.create_task()` background tasks
- **Benefits:**
  - Don't block the HTTP response
  - Can continue processing even if webhook fails
  - Multiple webhooks can run in parallel

## Files Modified

1. **`requirements.txt`**
   - Added `httpx==0.25.2` for async HTTP client

2. **`main.py`**
   - Added `import httpx`
   - Modified `receive_sms()` to return immediately
   - Changed `send_discord_alert()` to use async httpx with timeout
   - Changed `send_price_alert_to_discord()` to use async httpx with timeout
   - Changed `_post_discord_message()` to async with httpx
   - All Discord calls now run in background tasks

3. **`alternative_channel.py`**
   - Replaced `requests` with `httpx`
   - Updated `send_to_alternative_channel()` to use async httpx with timeout

## Installation Steps

1. **Install new dependency:**
   ```bash
   pip install httpx==0.25.2
   ```
   Or update all dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. **Restart the server:**
   ```bash
   # Stop current server (Ctrl+C)
   # Start again
   python main.py
   ```

## Testing

### Test 1: Verify Immediate Response
1. Send a test SMS from your phone
2. Check Tasker logs - should see success immediately (no timeout)
3. Check server logs - should see "SMS received and processing" quickly
4. Discord message should still arrive (may take a few seconds)

### Test 2: Check Discord Messages
1. Send a test alert SMS
2. Wait 5-10 seconds
3. Check Discord channels:
   - Main channel should receive alert
   - Alternative channel should receive alert (if configured and rules match)

### Test 3: Check Logs
Look for these log messages:
- `"SMS received and processing"` - Immediate response
- `"Discord alert sent to {symbol} webhook successfully"` - Success
- `"Discord webhook timeout after 10 seconds"` - If Discord is slow (shouldn't happen often)
- `"Error sending Discord alert"` - If there's a problem

## Expected Behavior After Fix

### Before Fix:
```
Tasker → Server → [Wait 30+ seconds] → Timeout Error ❌
```

### After Fix:
```
Tasker → Server → [Immediate Response < 1 second] ✅
                → [Background Processing] → Discord ✅
```

## Troubleshooting

### If Tasker Still Times Out

1. **Check server is running:**
   ```bash
   # Check if server is listening on port 8000
   curl http://localhost:8000/
   ```

2. **Check network connectivity:**
   - Ensure phone can reach server (ngrok URL still valid?)
   - Test with browser: `https://your-ngrok-url.ngrok.io/`

3. **Check server logs:**
   - Look for errors in `trade_alerts.log`
   - Check for database lock errors
   - Check for import errors (httpx not installed?)

### If Discord Messages Not Arriving

1. **Check webhook URLs:**
   - Verify `discord_webhooks.json` has valid URLs
   - Test webhook manually:
     ```bash
     curl -X POST "WEBHOOK_URL" \
       -H "Content-Type: application/json" \
       -d '{"content": "Test message"}'
     ```

2. **Check logs for errors:**
   - Look for "Failed to send Discord alert" messages
   - Check for timeout errors
   - Verify toggle settings (alerts might be disabled)

3. **Check alert toggles:**
   - Visit `http://localhost:8000/admin/alerts`
   - Ensure relevant tags are enabled

### If Alternative Channel Not Working

1. **Check webhook configured:**
   ```bash
   curl http://localhost:8000/config/alternative-channel-webhook
   ```

2. **Check rules:**
   - Alternative channel only sends 1MIN and 5MIN EMA signals
   - 1MIN requires 5MIN confluence
   - Time filter: 6 AM - 1 PM PST/PDT
   - Weekend filter: No alerts on Sat/Sun

3. **Check logs:**
   - Look for "ALTERNATIVE CHANNEL" log messages
   - Check for filtering reasons

## Performance Improvements

- **Response Time:** < 1 second (was 30+ seconds)
- **Concurrent Requests:** Can handle multiple SMS simultaneously
- **Timeout Protection:** 10-second limit prevents indefinite hangs
- **Error Recovery:** Background tasks continue even if one fails

## Monitoring

Watch these log patterns:

**Good:**
```
INFO: Received SMS from ...
INFO: SMS received and processing
INFO: Discord alert sent to SPY webhook successfully
```

**Warning (but not fatal):**
```
WARNING: Discord webhook timeout after 10 seconds
ERROR: Failed to send Discord alert: 404
```

**Bad (needs investigation):**
```
ERROR: Error processing SMS: ...
ERROR: Database locked
ERROR: Import httpx failed
```

## Next Steps

1. **Install httpx:** `pip install httpx==0.25.2`
2. **Restart server**
3. **Test with a real SMS**
4. **Monitor logs for first few messages**
5. **Verify Discord messages arrive**

## Rollback (If Needed)

If issues occur, you can temporarily revert by:
1. Changing `asyncio.create_task()` back to `await`
2. Reverting httpx back to requests
3. But this will bring back timeout issues

**Recommendation:** Keep the fixes and troubleshoot any new issues that arise.

---

**Last Updated:** After timeout fix implementation
**Status:** Ready for testing

