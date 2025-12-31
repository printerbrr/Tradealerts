# Troubleshooting: Messages Not Reaching Server

## Problem
Messages from Tasker are not reaching the server. Tasker shows timeout errors, and no Discord messages are received.

## Diagnostic Approach

### Step 1: Verify Server is Running and Accessible

**Check if server is running:**
```bash
# On your server/deployment platform
# Check if process is running
ps aux | grep python
# Or check if port is listening
netstat -an | grep 8000
# Or on Windows
netstat -an | findstr 8000
```

**Test server health endpoint:**
```bash
# From your phone's browser or computer
curl https://your-ngrok-url.ngrok.io/
# Should return: {"message":"Trade Alerts SMS Parser","status":"healthy",...}
```

**Check server logs:**
- Look for startup messages
- Check for any error messages
- Verify the server started successfully

**Possible Issues:**
- ❌ Server crashed on startup
- ❌ Port conflict
- ❌ Database lock preventing startup
- ❌ Missing dependencies (httpx not installed?)

---

### Step 2: Verify Tasker Can Reach Server

**Test from Tasker manually:**
1. Open Tasker
2. Create a test task with HTTP Request action
3. Use GET method to test: `https://your-ngrok-url.ngrok.io/`
4. Check if you get a response

**Check ngrok URL:**
- ngrok URLs change on restart (free tier)
- Verify current ngrok URL matches Tasker configuration
- Test ngrok URL in browser first

**Network connectivity:**
- Phone must have internet connection
- Check if phone can reach ngrok.io
- Try from different network (WiFi vs cellular)

**Possible Issues:**
- ❌ ngrok URL changed (most common!)
- ❌ Phone has no internet
- ❌ Firewall blocking ngrok
- ❌ Tasker doesn't have internet permission

---

### Step 3: Check Server Logs for Incoming Requests

**What to look for in logs:**

**GOOD - Request received:**
```
INFO: Raw request body: b'{"sender":"...","message":"..."}'
INFO: Received SMS from ...
```

**BAD - No logs at all:**
- Request never reached server
- Check network/ngrok/Tasker configuration

**BAD - Error before logging:**
```
ERROR: Error processing SMS: ...
```
- Something crashed before we could log the request
- Check full stack trace

**Check log file:**
```bash
# On server
tail -f trade_alerts.log
# Or check recent entries
tail -n 100 trade_alerts.log
```

**Possible Issues:**
- ❌ Request blocked by firewall
- ❌ ngrok not forwarding requests
- ❌ Server crashed before logging
- ❌ Exception in request handling

---

### Step 4: Verify Tasker Configuration

**Check Tasker HTTP Request action:**

1. **Method:** Must be `POST`
2. **URL:** Must be exact endpoint:
   ```
   https://your-ngrok-url.ngrok.io/webhook/sms
   ```
   - No trailing slash
   - Must be `/webhook/sms` (not `/webhook/sms/`)
   - Must be HTTPS (not HTTP)

3. **Headers:** Must include:
   ```
   Content-Type: application/json
   ```

4. **Body:** Must be valid JSON:
   ```json
   {
     "sender": "%SMSRF",
     "message": "%SMSRB",
     "timestamp": "%DATE %TIME"
   }
   ```

5. **Timeout:** Check Tasker timeout settings
   - Default is 30 seconds
   - If server takes longer, Tasker will timeout
   - Our fix should make response < 1 second

**Common Tasker Mistakes:**
- ❌ Wrong URL (typo, old ngrok URL)
- ❌ Missing Content-Type header
- ❌ Malformed JSON in body
- ❌ Using GET instead of POST
- ❌ Tasker profile not enabled
- ❌ Tasker doesn't have SMS permission

---

### Step 5: Test Endpoint Directly

**Test with curl (from computer):**
```bash
curl -X POST https://your-ngrok-url.ngrok.io/webhook/sms \
  -H "Content-Type: application/json" \
  -d '{"sender":"test","message":"ALERT ON SPY 5MIN TF 921 EMA Cross"}'
```

**Expected response:**
```json
{"status":"success","message":"SMS received and processing"}
```

**If this works but Tasker doesn't:**
- Tasker configuration issue
- Tasker network/permission issue

**If this doesn't work:**
- Server/endpoint issue
- Check server logs for errors

---

### Step 6: Check for Blocking Operations

**Current code still has blocking operations BEFORE return:**

1. **Line 654:** `state_manager.get_timeframe_state()` - Synchronous DB call
2. **Line 660:** `update_system_state()` - Synchronous DB call
3. **Line 665:** `analyze_data()` - May call DB

**For 1MIN signals specifically:**
- Alternative channel checks 5MIN state (line 104 in alternative_channel.py)
- This happens in background task, but...
- The main endpoint still does DB calls before returning

**Check if database is locked:**
```bash
# Check database file
ls -la market_states.db
# Check if another process has it open
lsof market_states.db  # Linux/Mac
# Or check for .db-wal file (WAL mode)
ls -la market_states.db-wal
```

**Possible Issues:**
- ❌ Database locked by another process
- ❌ Database file permissions
- ❌ Database corruption
- ❌ Slow database operations blocking response

---

### Step 7: Check Server Response Time

**Add timing logs to endpoint:**
- Log when request arrives
- Log when response is sent
- Calculate time difference

**If response takes > 1 second:**
- Something is blocking before return
- Database operations likely culprit
- Need to move more to background

**If response is fast but Tasker still times out:**
- Network issue
- Tasker configuration issue
- Response not reaching Tasker

---

### Step 8: Verify Deployment

**Check if latest code is deployed:**
```bash
# Check git commit
git log -1
# Should see: "Fix Tasker timeout: Use async httpx..."

# Check if httpx is installed
pip list | grep httpx
# Should show: httpx 0.25.2

# Check if server restarted after deploy
# Look at server startup logs
```

**Possible Issues:**
- ❌ Old code still running
- ❌ httpx not installed
- ❌ Server didn't restart after deploy
- ❌ Deployment failed silently

---

## Diagnostic Checklist

Use this checklist to systematically diagnose:

- [ ] **Server Status**
  - [ ] Server process is running
  - [ ] Health endpoint responds (`GET /`)
  - [ ] No startup errors in logs

- [ ] **Network Connectivity**
  - [ ] ngrok URL is current and working
  - [ ] Can reach server from browser
  - [ ] Phone has internet connection
  - [ ] Test endpoint with curl works

- [ ] **Tasker Configuration**
  - [ ] URL is correct and current
  - [ ] Method is POST
  - [ ] Headers include Content-Type
  - [ ] Body is valid JSON
  - [ ] Profile is enabled
  - [ ] Tasker has SMS permission

- [ ] **Server Logs**
  - [ ] See "Raw request body" in logs (request received)
  - [ ] See "Received SMS from" in logs (parsing worked)
  - [ ] No errors before return statement
  - [ ] Check for database lock errors

- [ ] **Code Deployment**
  - [ ] Latest code is deployed
  - [ ] httpx is installed
  - [ ] Server restarted after deploy

---

## Quick Tests

### Test 1: Server Health
```bash
curl https://your-ngrok-url.ngrok.io/
```
**Expected:** JSON response with status "healthy"

### Test 2: Endpoint Directly
```bash
curl -X POST https://your-ngrok-url.ngrok.io/webhook/sms \
  -H "Content-Type: application/json" \
  -d '{"sender":"test","message":"test message"}'
```
**Expected:** `{"status":"success","message":"SMS received and processing"}`

### Test 3: Check Logs
```bash
tail -f trade_alerts.log
# Then send test message
# Should see logs immediately
```

### Test 4: Tasker Test Task
Create simple Tasker task:
- HTTP Request → GET → `https://your-ngrok-url.ngrok.io/`
- Flash result
- If this works, network is fine
- If this fails, network/ngrok issue

---

## Most Likely Causes (Ranked)

1. **ngrok URL Changed** (90% likely)
   - Free ngrok URLs change on restart
   - Tasker still using old URL
   - **Fix:** Update Tasker with new ngrok URL

2. **Server Not Running** (5% likely)
   - Server crashed
   - Deployment failed
   - **Fix:** Restart server, check logs

3. **Database Lock** (3% likely)
   - Database locked by another process
   - Slow database operations
   - **Fix:** Check database, restart if needed

4. **Tasker Configuration Error** (2% likely)
   - Wrong URL, method, headers, or body
   - **Fix:** Verify Tasker configuration

5. **Network/Firewall Issue** (<1% likely)
   - Phone can't reach server
   - Firewall blocking
   - **Fix:** Test from different network

---

## Next Steps Based on Diagnosis

### If No Logs Appear (Request Not Reaching Server)
1. Verify ngrok URL is current
2. Test endpoint with curl
3. Check Tasker configuration
4. Verify phone internet connection

### If Logs Show Request But No Response
1. Check for exceptions in logs
2. Check database lock status
3. Verify httpx is installed
4. Check if code is latest version

### If Response is Slow (>1 second)
1. Move database operations to background
2. Check database performance
3. Check for blocking operations

### If Everything Works But No Discord Messages
1. Check Discord webhook URLs
2. Check alert toggles
3. Check time/weekend filters
4. Check Discord webhook logs

---

## Emergency Debugging: Add More Logging

If still stuck, add this at the very start of `receive_sms()`:

```python
@app.post("/webhook/sms", tags=["Ingest"], include_in_schema=False) 
async def receive_sms(request: Request):
    logger.info("=" * 50)
    logger.info("SMS ENDPOINT CALLED - REQUEST RECEIVED")
    logger.info(f"Request method: {request.method}")
    logger.info(f"Request URL: {request.url}")
    logger.info(f"Request headers: {dict(request.headers)}")
    logger.info("=" * 50)
    try:
        # ... rest of code
```

This will confirm if requests are reaching the endpoint.

---

**Last Updated:** After timeout fix deployment
**Status:** Diagnostic guide - no code changes yet

