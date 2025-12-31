# How to Remove Debug Logging

All debug logging is controlled by a single flag: `DEBUG_LOGGING`

## Quick Removal (Once Bug is Fixed)

### Step 1: Disable Debug Logging
In `main.py`, find line ~32 and change:
```python
DEBUG_LOGGING = True  # TODO: Set to False once bug is fixed
```
to:
```python
DEBUG_LOGGING = False  # Bug fixed - debug logging disabled
```

### Step 2: Disable in Alternative Channel
In `alternative_channel.py`, find the DEBUG_LOGGING flag (around line 18) and set:
```python
DEBUG_LOGGING = False
```

That's it! All debug logging will be disabled but the code remains for future debugging.

---

## Complete Removal (If You Want to Delete All Debug Code)

If you want to completely remove all debug logging code (not recommended - better to just disable), search for these markers:

### In `main.py`:
1. **Search for:** `# ============================================================================`
2. **Search for:** `# DEBUG LOGGING:`
3. **Remove all blocks** between these markers

### Key Sections to Remove:

1. **Lines ~27-60:** Debug logging imports and configuration
   - Look for: `DEBUG_LOGGING = True`
   - Look for: `if DEBUG_LOGGING:` blocks

2. **Lines ~70-120:** Request middleware
   - Look for: `@app.middleware("http")` with `log_requests`

3. **Lines ~120-140:** Database status checker
   - Look for: `def log_database_status():`

4. **In `receive_sms()` function:**
   - All `if DEBUG_LOGGING:` blocks
   - Request ID tracking
   - Step-by-step logging

5. **In `send_discord_alert()` function:**
   - All `if DEBUG_LOGGING:` blocks
   - Alert ID tracking

6. **In startup function:**
   - Health check task
   - Startup/shutdown logging

### In `alternative_channel.py`:
1. **Lines ~18-20:** DEBUG_LOGGING flag
2. **In `send_to_alternative_channel()` function:**
   - All `if DEBUG_LOGGING:` blocks

---

## What Debug Logging Adds

When `DEBUG_LOGGING = True`, you get:

1. **Request Tracking:**
   - Unique request IDs for each request
   - Request start/end times
   - Step-by-step progress through SMS endpoint

2. **Database Operation Timing:**
   - How long each DB operation takes
   - Database status checks
   - Lock detection

3. **Background Task Tracking:**
   - When tasks are created
   - Task execution status
   - Task errors with full tracebacks

4. **Periodic Health Checks:**
   - Every 30 seconds: server status
   - Active task count
   - Database connectivity

5. **Enhanced Error Logging:**
   - Full stack traces
   - Request context in errors
   - Timing information

---

## Recommended Approach

**Don't delete the code** - just set `DEBUG_LOGGING = False` in both files. This way:
- Code is preserved for future debugging
- Easy to re-enable if issues return
- No risk of breaking anything
- Minimal performance impact when disabled

---

## Files Modified

1. **main.py:**
   - Added DEBUG_LOGGING flag (line ~32)
   - Added request middleware
   - Added database status checker
   - Enhanced SMS endpoint logging
   - Enhanced Discord alert logging
   - Added periodic health check
   - Added startup/shutdown logging

2. **alternative_channel.py:**
   - Added DEBUG_LOGGING flag (line ~18)
   - Enhanced alternative channel logging

---

**Last Updated:** After adding comprehensive debug logging
**Status:** Ready for troubleshooting

