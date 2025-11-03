import os
import re
from fastapi import FastAPI, HTTPException, Request, Body
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import logging
from datetime import datetime
import json

# Import state tracking modules
from state_manager import state_manager
from confluence_rules import confluence_rules
from webhook_manager import webhook_manager
from alert_toggle_manager import alert_toggle_manager

# NEW: imports for scheduler/timezone
import asyncio
import pytz

# Configure logging
logging.basicConfig(
    level=logging.INFO,  # Production logging level
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('trade_alerts.log'),  # Production log file
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Trade Alerts SMS Parser",
    description="A lean SMS-based trade alerting system with enhanced state management",
    version="2.0.0"
)

# Data models
class SMSMessage(BaseModel):
    sender: str
    message: str
    timestamp: Optional[str] = None
    phone_number: Optional[str] = None

class AlertConfig(BaseModel):
    enabled: bool = True
    parameters: Dict[str, Any] = {}
    discord_webhook_url: Optional[str] = None

class TimeFilterToggle(BaseModel):
    enabled: bool  # True = enforce time window; False = ignore_time_filter

class TestFiltersToggle(BaseModel):
    """Toggle both time filter (5am-1pm) and weekend filter for testing"""
    time_filter_enabled: bool = True  # True = enforce 5am-1pm window; False = ignore
    weekend_filter_enabled: bool = True  # True = enforce weekend filter; False = ignore

class WebhookMapping(BaseModel):
    symbol: str
    url: str

class AddTickerRequest(BaseModel):
    symbol: str
    webhook_url: str

class WebhookUpdateRequest(BaseModel):
    webhook_url: str

class RefreshStatesRequest(BaseModel):
    symbols: List[str] = []
    timeframes: List[str] = ["5MIN","15MIN","30MIN","1HR","4HR","1DAY"]
    ema_pairs: List[List[int]] = [[9,21]]
    lookback_days: int = 30

# Global configuration (will be loaded from environment/config file)
alert_config = AlertConfig()

# Production settings
PRODUCTION_MODE = True
PRODUCTION_PORT = 8000
PRODUCTION_DATABASE = "market_states.db"
PRODUCTION_LOG_FILE = "trade_alerts.log"

# Load Discord webhook URL from environment variable or config file
discord_webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")

# If not in environment, try to load from config file
if not discord_webhook_url:
    try:
        with open("discord_config.txt", "r") as f:
            discord_webhook_url = f.read().strip()
    except FileNotFoundError:
        pass

if discord_webhook_url:
    alert_config.discord_webhook_url = discord_webhook_url
    logger.info(f"Discord webhook URL loaded: {discord_webhook_url[:50]}...")
else:
    logger.warning("DISCORD_WEBHOOK_URL not found in environment or config file")

# Initialize state tracking system
try:
    # Initialize database with production path
    state_manager.database_path = PRODUCTION_DATABASE
    state_manager.init_database()
    logger.info(f"State manager initialized with database: {PRODUCTION_DATABASE}")
    # Rebuild current timeframe states from recorded crossover history
    try:
        state_manager.bootstrap_from_history()
        logger.info("State bootstrap from history completed")
    except Exception as e:
        logger.warning(f"State bootstrap from history failed: {e}")
    
    # Load confluence rules
    confluence_rules.load_rules()
    # Ensure MACD next-higher EMA confluence rule is enabled
    try:
        updated = False
        for rule in confluence_rules.rules:
            name = rule.get('name', '').lower()
            if 'macd confluence with next higher ema' in name:
                if not rule.get('enabled', False):
                    rule['enabled'] = True
                    updated = True
        if updated:
            confluence_rules.save_rules()
            logger.info("Enabled MACD next-higher EMA confluence rule")
    except Exception as e:
        logger.warning(f"Could not enforce MACD confluence rule enablement: {e}")
    logger.info(f"Confluence rules engine initialized")
    
    # Initialize webhook manager (will auto-create config if needed)
    webhook_manager.load_webhooks()
    logger.info(f"Webhook manager initialized")
    # Ensure toggle defaults exist for configured symbols
    try:
        symbols_for_toggles = webhook_manager.get_all_symbols() or ["SPY"]
        for sym in symbols_for_toggles:
            alert_toggle_manager.ensure_defaults(sym)
    except Exception as e:
        logger.warning(f"Toggle defaults init skipped: {e}")
    
except Exception as e:
    logger.error(f"Failed to initialize state tracking system: {e}")

@app.get("/", tags=["Root"])
async def root():
    return {
        "message": "Trade Alerts SMS Parser", 
        "status": "healthy",
        "version": "2.0.0",
        "mode": "production"
    }

@app.post("/webhook/sms", tags=["Ingest"], include_in_schema=False) 
async def receive_sms(request: Request):
    """
    Webhook endpoint to receive SMS messages forwarded from Tasker
    """
    try:
        # Get raw body to handle malformed JSON
        body = await request.body()
        logger.info(f"Raw request body: {body}")
        
        # Decode body string first
        body_str = body.decode('utf-8', errors='ignore')
        
        # Try to fix JSON by escaping unescaped newlines and control characters in string values
        # This handles cases where the JSON contains literal newlines instead of \n
        def fix_json_strings(json_str: str) -> str:
            """Fix JSON by properly escaping control characters in string values"""
            result = []
            in_string = False
            escape_next = False
            i = 0
            
            while i < len(json_str):
                char = json_str[i]
                
                if escape_next:
                    result.append(char)
                    escape_next = False
                    i += 1
                    continue
                
                if char == '\\':
                    escape_next = True
                    result.append(char)
                    i += 1
                    continue
                
                # Check for unescaped quote (toggle string state)
                if char == '"' and (i == 0 or json_str[i-1] != '\\'):
                    in_string = not in_string
                    result.append(char)
                    i += 1
                    continue
                
                if in_string:
                    # Inside a string value - escape ALL control characters
                    char_ord = ord(char)
                    # Check if it's a control character (0x00-0x1F) or other problematic chars
                    if char == '\n':
                        result.append('\\n')
                    elif char == '\r':
                        result.append('\\r')
                    elif char == '\t':
                        result.append('\\t')
                    elif char == '\b':
                        result.append('\\b')
                    elif char == '\f':
                        result.append('\\f')
                    elif char == '"':
                        # Shouldn't happen if we're tracking quotes correctly, but escape it
                        result.append('\\"')
                    elif 0 <= char_ord < 32:
                        # Other control characters - escape as Unicode
                        result.append(f'\\u{char_ord:04x}')
                    else:
                        result.append(char)
                else:
                    # Outside string - keep as-is
                    result.append(char)
                
                i += 1
            
            return ''.join(result)
        
        # Try to parse JSON directly from raw body (don't use request.json() as it fails on control chars)
        sender = "unknown"
        message = ""
        
        try:
            # First try to parse the raw body as-is
            data = json.loads(body_str)
            sender = data.get("sender", "unknown")
            message = data.get("message", "")
            logger.debug("Successfully parsed JSON without fixing")
        except json.JSONDecodeError as json_error:
            # Initial parse failed - this is expected for malformed JSON, try to fix it
            logger.debug(f"Initial JSON parse failed (will attempt fix): {json_error}")
            
            try:
                # Try to fix the JSON by escaping control characters
                fixed_json = fix_json_strings(body_str)
                data = json.loads(fixed_json)
                sender = data.get("sender", "unknown")
                message = data.get("message", "")
                logger.info("Successfully parsed JSON after fixing control characters")
            except Exception as fixed_json_error:
                # Both attempts failed - this is a real problem
                logger.warning(f"JSON parse failed: Initial error - {json_error}, Fixed JSON also failed - {fixed_json_error}")
                logger.debug(f"Raw body string: {body_str[:500]}...")
                # Fallback: try to extract from raw body using regex
                sender = "unknown"
                message = body_str
                
                # Try to extract sender and message from malformed JSON
                if '"sender"' in body_str:
                    try:
                        # Handle both "sender":"value" and "sender": "value" formats
                        sender_patterns = [
                            r'"sender"\s*:\s*"([^"]+)"',
                            r'"sender"\s*:\s*"([^"]*)"'
                        ]
                        for pattern in sender_patterns:
                            match = re.search(pattern, body_str)
                            if match:
                                sender = match.group(1)
                                break
                    except:
                        pass
                
                if '"message"' in body_str:
                    try:
                        # Improved message extraction to handle unescaped quotes and multiline content
                        # First try to find the message field and extract everything until the next field or end
                        msg_start_pattern = r'"message"\s*:\s*"'
                        msg_start_match = re.search(msg_start_pattern, body_str)
                        
                        if msg_start_match:
                            start_pos = msg_start_match.end()
                            # Find the end of the message field by looking for the next field or closing brace
                            # Handle multiline content by looking for closing quote before next field/brace
                            
                            remaining_text = body_str[start_pos:]
                            
                            # Look for patterns that indicate end of message field
                            # Use multiline mode to handle newlines in the message
                            end_patterns = [
                                r'\n\s*",\s*"[^"]*"\s*:',  # message ends with newline, ", followed by another field
                                r'\n\s*"\s*}',             # message ends with newline, " followed by closing brace
                                r'",\s*"[^"]*"\s*:',       # message ends with ", followed by another field (no newline)
                                r'",\s*}',                 # message ends with ", followed by closing brace
                                r'"\s*}',                  # message ends with " followed by closing brace
                                r'",\s*$',                 # message ends with ", at end of string
                                r'"\s*$'                  # message ends with " at end of string
                            ]
                            
                            message_end_pos = len(remaining_text)
                            for pattern in end_patterns:
                                end_match = re.search(pattern, remaining_text, re.MULTILINE)
                                if end_match:
                                    message_end_pos = end_match.start()
                                    break
                            
                            # Extract the message content
                            message_content = remaining_text[:message_end_pos]
                            
                            # Clean up escaped characters - convert literal \n to actual newline for display
                            # But preserve the content as-is (newlines in message are intentional)
                            message = message_content.replace('\\n', '\n').replace('\\"', '"').replace('\\t', '\t').replace('\\r', '\r')
                            
                            # Don't remove control characters that are legitimate parts of the message
                            # Only remove truly problematic control characters (null bytes, etc.)
                            message = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', message)  # Keep \n, \r, \t
                            
                            logger.info(f"Extracted message from malformed JSON: {message[:100]}...")
                            
                    except Exception as extract_error:
                        logger.warning(f"Failed to extract message from malformed JSON: {extract_error}")
                        # Keep the original body_str as fallback
                        message = body_str
        
        logger.info(f"Received SMS from {sender}: {message}")
        
        # Parse the SMS data
        parsed_data = parse_sms_data(message)
        
        # Log the parsed data
        log_data = {
            "timestamp": datetime.now().isoformat(),
            "sender": sender,
            "original_message": message,
            "parsed_data": parsed_data
        }
        
        logger.info(f"Parsed data: {json.dumps(log_data, indent=2)}")
        
        # Update system state based on detected crossovers
        update_system_state(parsed_data)
        
        # Analyze the data and check for alerts
        if alert_config.enabled:
            alert_triggered = analyze_data(parsed_data)
            if alert_triggered:
                await send_discord_alert(log_data)
        
        return {"status": "success", "message": "SMS processed successfully"}
        
    except Exception as e:
        logger.error(f"Error processing SMS: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

def parse_sms_data(message: str) -> Dict[str, Any]:
    """
    Parse SMS message data based on configured rules
    Optimized for Schwab alerts and other trading signals
    """
    import re
    
    parsed = {
        "raw_message": message,
        "symbol": None,
        "price": None,
        "action": None,
        "confidence": None,
        "timeframe": None,
        "alert_type": None,
        "trigger_time": None,
        "study_details": None
    }
    
    message_lower = message.lower()
    
    # Schwab Alert Detection
    if "schwab" in message_lower or "alert on" in message_lower:
        parsed["alert_type"] = "schwab_alert"
        
        # Extract symbol (usually after "ALERT ON")
        symbol_match = re.search(r'ALERT ON (\w+)', message, re.IGNORECASE)
        if symbol_match:
            parsed["symbol"] = symbol_match.group(1)
        
        # Extract price (MARK = value) - be more specific to avoid false matches
        price_match = re.search(r'MARK\s*=\s*([\d.]+)', message, re.IGNORECASE)
        if price_match:
            parsed["price"] = float(price_match.group(1))
        
        # Extract timeframe - handle all formats: 1MIN/1M, 5MIN/5M, 15MIN/15M, 30MIN/30M, 1HR/1H/1HOUR, 2HR/2H/2HOUR, 4HR/4H/4HOUR, 1D/1DAY/1 DAY
        tf_match = re.search(r'(\d+(?:\s+)?(?:MIN|M|HR|HOUR|H|DAY|D))\s*TF', message, re.IGNORECASE)
        if tf_match:
            timeframe_raw = tf_match.group(1).strip().upper()
            
            # Normalize timeframe formats for consistent display
            timeframe_map = {
                'M': 'MIN',
                'H': 'HR', 
                'D': 'DAY'
            }
            
            # Remove extra spaces first
            timeframe_raw = re.sub(r'\s+', '', timeframe_raw)
            
            # Replace single letter abbreviations with full words
            for abbrev, full in timeframe_map.items():
                if timeframe_raw.endswith(abbrev) and len(timeframe_raw) > 1:
                    timeframe_raw = timeframe_raw[:-1] + full
            
            parsed["timeframe"] = timeframe_raw
        
        # Extract EMA pair from "TF XXX" pattern (e.g., "5MIN TF 921" = 9/21 EMAs)
        ema_tf_match = re.search(r'TF\s*(\d{3,4})', message, re.IGNORECASE)
        if ema_tf_match:
            ema_code = ema_tf_match.group(1)
            # Parse 3-4 digit codes: 921 = 9/21, 950 = 9/50, 2150 = 21/50
            if len(ema_code) == 3:
                parsed["ema_short"] = int(ema_code[0])
                parsed["ema_long"] = int(ema_code[1:])
            elif len(ema_code) == 4:
                parsed["ema_short"] = int(ema_code[:2])
                parsed["ema_long"] = int(ema_code[2:])
        
        # Extract trigger time
        time_match = re.search(r'SUBMIT AT (\d+/\d+/\d+ \d+:\d+:\d+)', message, re.IGNORECASE)
        if time_match:
            parsed["trigger_time"] = time_match.group(1)
        
        # Extract study details
        study_match = re.search(r'STUDY\s*=\s*([\d.]+)', message, re.IGNORECASE)
        if study_match:
            parsed["study_details"] = study_match.group(1)
        
        # Detect MACD crossover signals first
        macd_keywords = ["macdhistogramcrossover", "macd crossover", "macd cross"]
        
        if any(keyword in message_lower for keyword in macd_keywords):
            parsed["action"] = "macd_crossover"
            
            # Extract MACD crossover direction
            if "negative to positive" in message_lower:
                parsed["macd_direction"] = "bullish"
            elif "positive to negative" in message_lower:
                parsed["macd_direction"] = "bearish"
            else:
                # Default to bullish if direction not specified
                parsed["macd_direction"] = "bullish"
        
        # Detect EMA crossover signals - improved detection
        elif any(keyword in message_lower for keyword in ["movingavgcrossover", "crossover", "ema cross", "moving average", "length1", "length2", "exponential"]):
            parsed["action"] = "moving_average_crossover"
            
            # Extract EMA details
            ema_match = re.search(r'"length1"\s*=\s*(\d+).*?"length2"\s*=\s*(\d+)', message, re.IGNORECASE)
            if ema_match:
                parsed["ema_short"] = int(ema_match.group(1))
                parsed["ema_long"] = int(ema_match.group(2))
            
            # Also try simpler pattern
            elif "ema cross" in message_lower:
                parsed["ema_short"] = 9  # Default for Schwab
                parsed["ema_long"] = 21  # Default for Schwab
            
            # Extract EMA crossover direction
            if "negative to positive" in message_lower or "bullish" in message_lower:
                parsed["ema_direction"] = "bullish"
            elif "positive to negative" in message_lower or "bearish" in message_lower:
                parsed["ema_direction"] = "bearish"
            else:
                # Default to bullish if direction not specified
                parsed["ema_direction"] = "bullish"
        
        # Set confidence based on study value
        if parsed["study_details"]:
            try:
                study_value = float(parsed["study_details"])
                if study_value >= 1.0:
                    parsed["confidence"] = "high"
                elif study_value >= 0.5:
                    parsed["confidence"] = "medium"
                else:
                    parsed["confidence"] = "low"
            except:
                parsed["confidence"] = "unknown"
    
    # Generic trading signal detection
    elif any(word in message_lower for word in ['buy', 'sell', 'long', 'short', 'alert']):
        parsed["action"] = "trade_signal"
        
        # Look for symbol patterns
        symbol_patterns = [
            r'\b([A-Z]{1,5})\b',  # 1-5 letter uppercase (AAPL, TSLA, etc.)
            r'\$([A-Z]{1,5})\b'   # $SYMBOL format
        ]
        
        for pattern in symbol_patterns:
            symbol_match = re.search(pattern, message)
            if symbol_match:
                parsed["symbol"] = symbol_match.group(1)
                break
        
        # Look for price patterns
        price_patterns = [
            r'\$(\d+\.?\d*)',      # $150.50
            r'at \$(\d+\.?\d*)',   # at $150.50
            r'price.*?(\d+\.?\d*)', # price 150.50
            r'(\d+\.?\d*)\s*\$'    # 150.50 $
        ]
        
        for pattern in price_patterns:
            price_match = re.search(pattern, message, re.IGNORECASE)
            if price_match:
                parsed["price"] = float(price_match.group(1))
                break
    
    return parsed

def update_system_state(parsed_data: Dict[str, Any]):
    """
    Update timeframe state based on detected crossover
    This function records EMA and MACD crossovers to maintain state tracking
    Checks current status and only updates if different
    """
    try:
        symbol = parsed_data.get('symbol', 'SPY')
        timeframe = parsed_data.get('timeframe')
        price = parsed_data.get('price')
        
        if not timeframe:
            logger.warning(f"No timeframe found for state update: {symbol}")
            return
        
        # Get current state for this symbol/timeframe
        current_state = state_manager.get_timeframe_state(symbol, timeframe)
        
        # Update MACD crossover state
        if parsed_data.get('action') == 'macd_crossover':
            direction = parsed_data.get('macd_direction', 'unknown')
            if direction in ['bullish', 'bearish']:
                # Check if current MACD status is different
                current_macd_status = current_state.get('macd_status', 'UNKNOWN') if current_state else 'UNKNOWN'
                
                if current_macd_status != direction.upper():
                    logger.info(f"MACD STATUS CHANGE DETECTED: {symbol} {timeframe} MACD {current_macd_status} -> {direction.upper()}")
                    success = state_manager.update_timeframe_state(
                        symbol, timeframe, 'macd', direction, price
                    )
                    if success:
                        logger.info(f"STATE UPDATE: {symbol} {timeframe} MACD -> {direction.upper()}")
                    else:
                        logger.error(f"Failed to update MACD state for {symbol} {timeframe}")
                else:
                    logger.info(f"MACD STATUS UNCHANGED: {symbol} {timeframe} MACD already {direction.upper()}")
        
        # Update EMA crossover state
        elif parsed_data.get('action') == 'moving_average_crossover':
            direction = parsed_data.get('ema_direction', 'unknown')
            if direction in ['bullish', 'bearish']:
                # Check if current EMA status is different
                current_ema_status = current_state.get('ema_status', 'UNKNOWN') if current_state else 'UNKNOWN'
                
                if current_ema_status != direction.upper():
                    logger.info(f"EMA STATUS CHANGE DETECTED: {symbol} {timeframe} EMA {current_ema_status} -> {direction.upper()}")
                    success = state_manager.update_timeframe_state(
                        symbol, timeframe, 'ema', direction, price
                    )
                    if success:
                        logger.info(f"STATE UPDATE: {symbol} {timeframe} EMA -> {direction.upper()}")
                    else:
                        logger.error(f"Failed to update EMA state for {symbol} {timeframe}")
                else:
                    logger.info(f"EMA STATUS UNCHANGED: {symbol} {timeframe} EMA already {direction.upper()}")
        
        else:
            logger.debug(f"No state update needed for action: {parsed_data.get('action')}")
            
    except Exception as e:
        logger.error(f"Error updating system state: {e}")

def analyze_data(parsed_data: Dict[str, Any]) -> bool:
    """
    Analyze parsed data against configured parameters
    Returns True if alert should be triggered
    Focus: MACD and EMA Crossover detection for tomorrow's trading
    """
    # Check if we should send alerts based on time (1 PM - 4:59 AM PST/PDT = no alerts)
    # Allow bypass via config for after-hours testing
    import pytz
    from datetime import datetime
    
    pacific = pytz.timezone('America/Los_Angeles')
    current_time_pacific = datetime.now(pacific)
    
    # Check for weekend (Saturday=5, Sunday=6) - market is closed
    # Allow bypass via config for testing
    if alert_config.parameters.get('ignore_weekend_filter', False):
        logger.info("Weekend filter bypassed via config (ignore_weekend_filter=true)")
    else:
        weekday = current_time_pacific.weekday()
        if weekday >= 5:  # Saturday (5) or Sunday (6)
            logger.info(f"ALERT FILTERED: Current day is weekend ({current_time_pacific.strftime('%A')}) - market is closed")
            return False
    
    if not alert_config.parameters.get('ignore_time_filter', False):
        current_hour = current_time_pacific.hour
        
        # No alerts between 1 PM (13:00) and 4:59 AM (4:59)
        if 13 <= current_hour or current_hour < 5:
            logger.info(f"ALERT FILTERED: Current time {current_time_pacific.strftime('%I:%M %p')} is outside alert hours (5 AM - 1 PM PST/PDT)")
            return False
    else:
        logger.info("Time filter bypassed via config (ignore_time_filter=true)")
    
    # Check confluence rules before sending alerts
    symbol = parsed_data.get('symbol', 'SPY')
    current_states = state_manager.get_all_states(symbol)
    
    if not confluence_rules.evaluate_alert(parsed_data, current_states):
        logger.info(f"ALERT FILTERED: Confluence requirements not met for {symbol}")
        return False
    
    # Primary focus: MACD Crossover detection
    if parsed_data.get("action") == "macd_crossover":
        logger.info("MACD CROSSOVER DETECTED! Triggering Discord alert")
        return True
    
    # Secondary focus: Moving Average Crossover detection
    if parsed_data.get("action") == "moving_average_crossover":
        logger.info("EMA CROSSOVER DETECTED! Triggering Discord alert")
        return True
    
    # Tertiary: High-confidence Schwab alerts
    if parsed_data.get("alert_type") == "schwab_alert" and parsed_data.get("confidence") == "high":
        logger.info("HIGH CONFIDENCE SCHWAB ALERT! Triggering Discord alert")
        return True
    
    # Fallback: Any trade signal
    if parsed_data.get("action") == "trade_signal":
        logger.info("TRADE SIGNAL DETECTED! Triggering Discord alert")
        return True
    
    return False

async def send_discord_alert(log_data: Dict[str, Any]):
    """
    Send alert to Discord webhook based on symbol
    """
    try:
        import requests
        
        parsed = log_data['parsed_data']
        symbol = parsed.get('symbol', 'SPY').upper()
        
        # Get webhook URL for this symbol
        webhook_url = webhook_manager.get_webhook(symbol)
        
        if not webhook_url:
            logger.warning(f"No Discord webhook configured for {symbol}")
            return
        
        # Simple, clean Discord message
        # Always use server receive time in PST/PDT (handles daylight savings automatically)
        import pytz
        from datetime import datetime
        
        # Get current time in Pacific timezone (handles PST/PDT automatically)
        pacific = pytz.timezone('America/Los_Angeles')
        server_time_pacific = datetime.now(pacific)
        # Determine if we're in DST (PDT) or not (PST)
        from datetime import timedelta
        dst_offset = server_time_pacific.dst()
        tz_abbrev = "PDT" if dst_offset and dst_offset != timedelta(0) else "PST"
        display_time = server_time_pacific.strftime("%I:%M %p") + f" {tz_abbrev}"
            
        # Create different message formats based on alert type
        if parsed.get('action') == 'macd_crossover':
            # MACD: custom compact format using next higher timeframe suffix
            macd_direction = (parsed.get('macd_direction', 'bullish') or 'bullish').lower()
            # Map direction to Call/Put
            direction_label = 'Call' if macd_direction == 'bullish' else 'Put'
            emoji = '🟢' if macd_direction == 'bullish' else '🔴'

            current_tf = (parsed.get('timeframe') or '').upper()
            next_tf = state_manager.get_next_higher_timeframe(current_tf) if current_tf else None

            def suffix_from_timeframe(tf: str) -> str:
                if not tf:
                    return ''
                tf = tf.upper()
                if tf.endswith('MIN'):
                    # '15MIN' -> '15'
                    return tf.replace('MIN', '')
                if tf.endswith('HR'):
                    # '1HR' -> '1h', '2HR' -> '2h', '4HR' -> '4h'
                    return tf.replace('HR', 'h').lower()
                if tf == '1DAY':
                    return '1d'
                return tf

            suffix = suffix_from_timeframe(next_tf)
            title_tf = current_tf or 'N/A'

            message = f"""@everyone
{emoji} {title_tf} MACD Cross - {direction_label}{suffix}
MARK: ${parsed.get('price', 'N/A')}
TIME: {display_time}"""

            # Build toggle tag for MACD using CALL/PUT + timeframe token
            macd_dir_label = 'CALL' if macd_direction == 'bullish' else 'PUT'
            macd_suffix = (suffix or '').upper().replace('H', 'H').replace('D', 'D')
            toggle_tag = f"{macd_dir_label}{macd_suffix}"
        else:
            # EMA Crossover format using confluence with next higher timeframe
            current_tf = (parsed.get('timeframe') or '').upper()
            next_tf = state_manager.get_next_higher_timeframe(current_tf) if current_tf else None
            ema_direction = (parsed.get('ema_direction', 'bullish') or 'bullish').lower()
            emoji = '🟢' if ema_direction == 'bullish' else '🔴'

            # Determine suffix token for the current timeframe (e.g., 30, 1H, 1D)
            def suffix_from_timeframe_for_tag(tf: str) -> str:
                if not tf:
                    return ''
                tf = tf.upper()
                if tf.endswith('MIN'):
                    # '15MIN' -> '15'
                    return tf.replace('MIN', '')
                if tf.endswith('HR'):
                    # '1HR' -> '1H'
                    return tf.replace('HR', 'H')
                if tf == '1DAY':
                    return '1D'
                return tf

            tag_suffix = suffix_from_timeframe_for_tag(current_tf)

            # Check next higher timeframe EMA alignment with current crossover direction
            states = state_manager.get_all_states(symbol)
            higher_ema_status = None
            if next_tf and next_tf in states:
                higher_ema_status = (states[next_tf].get('ema_status') or 'UNKNOWN').upper()

            # Determine alert tag per spec
            if ema_direction == 'bullish':
                tag = f"CALL{tag_suffix}" if higher_ema_status == 'BULLISH' else f"C{tag_suffix}"
            else:
                tag = f"PUT{tag_suffix}" if higher_ema_status == 'BEARISH' else f"P{tag_suffix}"

            title_tf = current_tf or 'N/A'
            message = f"""@everyone
{emoji} {title_tf} EMA Cross - {tag}
MARK: ${parsed.get('price', 'N/A')}
TIME: {display_time}"""

            toggle_tag = (tag or '').upper()
        
        # Respect per-symbol toggle before sending
        if toggle_tag and not alert_toggle_manager.is_enabled(symbol, toggle_tag):
            logger.info(f"ALERT BLOCKED by toggle: {symbol} {toggle_tag}")
            return
        
        payload = {
            "content": message
        }
        
        response = requests.post(
            webhook_url,
            json=payload,
            headers={"Content-Type": "application/json"}
        )
        
        if response.status_code == 204:
            logger.info(f"Discord alert sent to {symbol} webhook successfully")
        else:
            # Log detailed error information
            error_msg = f"Failed to send Discord alert: {response.status_code}"
            
            # Try to get response body for more details
            try:
                response_text = response.text
                if response_text:
                    error_msg += f" - Response: {response_text[:200]}"
            except:
                pass
            
            # Log webhook URL status (masked for security)
            webhook_display = webhook_url[:50] + "..." if len(webhook_url) > 50 else webhook_url
            error_msg += f" - Webhook: {webhook_display}"
            
            # Specific error messages for common status codes
            if response.status_code == 404:
                error_msg += " - Webhook URL not found. Possible causes: webhook deleted, invalid URL, or URL malformed."
            elif response.status_code == 401:
                error_msg += " - Unauthorized. Webhook URL may be invalid."
            elif response.status_code == 400:
                error_msg += " - Bad request. Check payload format."
            
            logger.error(error_msg)
            
    except Exception as e:
        logger.error(f"Error sending Discord alert: {str(e)}")
        # Also log the webhook URL (masked) if available
        try:
            webhook_display = webhook_url[:50] + "..." if len(webhook_url) > 50 else webhook_url
            logger.error(f"Webhook URL used: {webhook_display}")
        except:
            pass

# NEW: helper to post simple messages to a webhook
def _post_discord_message(webhook_url: str, content: str) -> bool:
    try:
        import requests
        resp = requests.post(webhook_url, json={"content": content}, headers={"Content-Type": "application/json"})
        if resp.status_code == 204:
            return True
        logger.warning(f"Discord post non-204: {resp.status_code}")
        return False
    except Exception as e:
        logger.error(f"Discord post failed: {e}")
        return False

# NEW: build EMA summary text for a symbol
def _build_ema_summary(symbol: str) -> str:
    states = state_manager.get_all_states(symbol)
    # Maintain display order
    order = ["1MIN","5MIN","15MIN","30MIN","1HR","2HR","4HR","1DAY"]
    def pretty_tf(tf: str) -> str:
        tfu = tf.upper()
        if tfu.endswith("MIN"):
            return tfu.replace("MIN", "Min")
        if tfu.endswith("HR"):
            return tfu.replace("HR", "Hr")
        if tfu == "1DAY":
            return "1Day"
        return tf
    lines: List[str] = []
    # Timestamp header in Pacific time
    pacific = pytz.timezone('America/Los_Angeles')
    now_pt = datetime.now(pacific)
    # Determine if we're in DST (PDT) or not (PST)
    from datetime import timedelta
    dst_offset = now_pt.dst()
    tz_abbrev = "PDT" if dst_offset and dst_offset != timedelta(0) else "PST"
    header = now_pt.strftime("%m/%d/%Y %I:%M %p") + f" {tz_abbrev}"
    lines.append(f"{header}")
    for tf in order:
        if tf in states:
            raw = (states[tf].get('ema_status') or 'UNKNOWN').upper()
            status = raw.capitalize()
            emoji = '🟢' if raw == 'BULLISH' else ('🔴' if raw == 'BEARISH' else '⚪')
            lines.append(f"{emoji} {pretty_tf(tf)} - {status}")
    return "\n".join(lines)

# NEW: job to send summary to each configured symbol's webhook
async def send_daily_ema_summaries():
    symbols = webhook_manager.get_all_symbols()
    if not symbols:
        symbols = ["SPY"]
    for sym in symbols:
        url = webhook_manager.get_webhook(sym)
        if not url:
            continue
        content = f"{sym} EMA States\n\n" + _build_ema_summary(sym)
        ok = _post_discord_message(url, content)
        if ok:
            logger.info(f"Daily EMA summary sent for {sym}")
        else:
            logger.warning(f"Failed to send daily EMA summary for {sym}")

# NEW: background scheduler that runs the job daily at 06:30 PT
async def _daily_scheduler_task():
    pacific = pytz.timezone('America/Los_Angeles')
    while True:
        now = datetime.now(pacific)
        
        # Check if it's a weekend (Saturday=5, Sunday=6) - skip sending on weekends
        weekday = now.weekday()
        if weekday >= 5:  # Saturday or Sunday
            # Calculate days until next Monday
            from datetime import timedelta
            days_until_monday = (7 - weekday) % 7
            if days_until_monday == 0:  # Already Monday (shouldn't happen, but safety check)
                days_until_monday = 7
            # Schedule for next Monday at 6:30 AM
            target = now.replace(hour=6, minute=30, second=0, microsecond=0)
            target = target + timedelta(days=days_until_monday)
            seconds = (target - now).total_seconds()
            logger.info(f"Weekend detected ({now.strftime('%A')}), scheduling next summary for Monday {target.strftime('%Y-%m-%d %I:%M %p %Z')}")
            await asyncio.sleep(seconds)
            continue
        
        # Calculate target time for today (or next weekday if past 6:30 AM)
        target = now.replace(hour=6, minute=30, second=0, microsecond=0)
        if now >= target:
            # schedule next day
            from datetime import timedelta
            target = target + timedelta(days=1)
            # If next day is weekend, skip to Monday
            while target.weekday() >= 5:
                target = target + timedelta(days=1)
        
        # sleep until target
        seconds = (target - now).total_seconds()
        try:
            await asyncio.sleep(seconds)
        except Exception:
            # in case of sleep interruption, retry quickly
            await asyncio.sleep(5)
            continue
        
        # After waking up, check if we already ran today (prevent duplicates)
        now_check = datetime.now(pacific)
        today_str = now_check.strftime('%Y-%m-%d')
        last_summary_date = state_manager.get_metadata('last_daily_summary_date')
        
        if last_summary_date == today_str:
            logger.warning(f"Daily EMA summary already sent today ({today_str}), skipping duplicate run")
            # Schedule for tomorrow (or next weekday)
            from datetime import timedelta
            target = now_check.replace(hour=6, minute=30, second=0, microsecond=0) + timedelta(days=1)
            while target.weekday() >= 5:  # Skip weekends
                target = target + timedelta(days=1)
            seconds = (target - now_check).total_seconds()
            await asyncio.sleep(seconds)
            continue
        
        try:
            await send_daily_ema_summaries()
            # Mark as sent for today (prevents duplicates)
            state_manager.set_metadata('last_daily_summary_date', today_str)
            logger.info(f"Daily EMA summary completed for {today_str}")
        except Exception as e:
            logger.error(f"Daily EMA summary job failed: {e}")
            # continue loop for next day

# NEW: startup hook to launch scheduler
@app.on_event("startup")
async def _start_scheduler():
    try:
        asyncio.create_task(_daily_scheduler_task())
        logger.info("Daily EMA summary scheduler started (06:30 PT)")
    except Exception as e:
        logger.error(f"Failed to start scheduler: {e}")

# NEW: optional admin endpoint to trigger summary immediately
@app.post("/admin/send-daily-ema-summaries", tags=["Admin"]) 
async def admin_send_daily_ema_summaries():
    try:
        await send_daily_ema_summaries()
        return {"status": "success"}
    except Exception as e:
        logger.error(f"admin send daily summaries failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/config", tags=["Config"], include_in_schema=False) 
async def get_config():
    """Get current configuration"""
    return alert_config

@app.post("/config", tags=["Config"], include_in_schema=False) 
async def update_config(config: AlertConfig):
    """Update configuration"""
    global alert_config
    alert_config = config
    logger.info(f"Configuration updated: {config}")
    return {"status": "success", "message": "Configuration updated"}

@app.post("/config/time_filter", tags=["Config"], include_in_schema=False) 
async def set_time_filter(toggle: TimeFilterToggle):
    """Enable/disable business-hours alert window (5 AM - 1 PM PT)."""
    # when enabled=True we enforce window → ignore_time_filter=False
    alert_config.parameters["ignore_time_filter"] = (not toggle.enabled)
    logger.info(f"Time filter enabled={toggle.enabled}")
    return {"status": "success", "enabled": toggle.enabled}

@app.post("/config/test-filters", tags=["Config"]) 
async def toggle_test_filters(toggle: TestFiltersToggle):
    """
    Toggle both time filter (5am-1pm PT) and weekend filter for testing purposes.
    
    - time_filter_enabled: True = enforce 5am-1pm window, False = ignore time filter
    - weekend_filter_enabled: True = enforce weekend filter, False = ignore weekend filter
    """
    # Set time filter
    alert_config.parameters["ignore_time_filter"] = (not toggle.time_filter_enabled)
    
    # Set weekend filter
    alert_config.parameters["ignore_weekend_filter"] = (not toggle.weekend_filter_enabled)
    
    logger.info(f"Test filters updated: time_filter_enabled={toggle.time_filter_enabled}, weekend_filter_enabled={toggle.weekend_filter_enabled}")
    
    return {
        "status": "success",
        "time_filter_enabled": toggle.time_filter_enabled,
        "weekend_filter_enabled": toggle.weekend_filter_enabled,
        "current_config": {
            "ignore_time_filter": alert_config.parameters.get("ignore_time_filter", False),
            "ignore_weekend_filter": alert_config.parameters.get("ignore_weekend_filter", False)
        }
    }

@app.get("/config/test-filters", tags=["Config"], include_in_schema=False)
async def get_test_filters():
    """Get current test filter settings"""
    return {
        "time_filter_enabled": not alert_config.parameters.get("ignore_time_filter", False),
        "weekend_filter_enabled": not alert_config.parameters.get("ignore_weekend_filter", False),
        "current_config": {
            "ignore_time_filter": alert_config.parameters.get("ignore_time_filter", False),
            "ignore_weekend_filter": alert_config.parameters.get("ignore_weekend_filter", False)
        }
    }

# Confluence Rules Management Endpoints
@app.get("/confluence/rules", include_in_schema=False)
async def get_confluence_rules():
    """Get current confluence rules configuration"""
    summary = confluence_rules.get_rule_summary()
    return summary

@app.get("/confluence/rules/{rule_index}", include_in_schema=False)
async def get_rule_details(rule_index: int):
    """Get details about a specific rule by index"""
    if 0 <= rule_index < len(confluence_rules.rules):
        return confluence_rules.rules[rule_index]
    raise HTTPException(status_code=404, detail="Rule not found")

@app.post("/confluence/rules/{rule_index}/enable", include_in_schema=False)
async def enable_rule(rule_index: int):
    """Enable a confluence rule"""
    if 0 <= rule_index < len(confluence_rules.rules):
        confluence_rules.rules[rule_index]['enabled'] = True
        confluence_rules.save_rules()
        rule_name = confluence_rules.rules[rule_index].get('name', f'Rule {rule_index}')
        logger.info(f"Enabled confluence rule: {rule_name}")
        return {"status": "success", "message": f"Rule '{rule_name}' enabled"}
    raise HTTPException(status_code=404, detail="Rule not found")

@app.post("/confluence/rules/{rule_index}/disable", include_in_schema=False)
async def disable_rule(rule_index: int):
    """Disable a confluence rule"""
    if 0 <= rule_index < len(confluence_rules.rules):
        confluence_rules.rules[rule_index]['enabled'] = False
        confluence_rules.save_rules()
        rule_name = confluence_rules.rules[rule_index].get('name', f'Rule {rule_index}')
        logger.info(f"Disabled confluence rule: {rule_name}")
        return {"status": "success", "message": f"Rule '{rule_name}' disabled"}
    raise HTTPException(status_code=404, detail="Rule not found")

@app.post("/confluence/rules/reload", include_in_schema=False)
async def reload_rules():
    """Reload confluence rules from file"""
    confluence_rules.reload_rules()
    logger.info("Confluence rules reloaded from file")
    return {"status": "success", "message": "Rules reloaded successfully"}

# Webhook Management Endpoints
@app.get("/webhooks", tags=["Webhooks"]) 
async def get_webhooks():
    """Get all configured webhooks"""
    config = webhook_manager.get_config()
    return config

@app.get("/webhooks/{symbol}", tags=["Webhooks"]) 
async def get_symbol_webhook(symbol: str):
    """Get webhook URL for a specific symbol"""
    webhook_url = webhook_manager.get_webhook(symbol)
    if webhook_url:
        # Don't expose full URL in response for security
        masked_url = f"{webhook_url[:50]}..." if len(webhook_url) > 50 else webhook_url
        return {
            "symbol": symbol.upper(),
            "webhook_configured": True,
            "webhook_preview": masked_url
        }
    return {"symbol": symbol.upper(), "webhook_configured": False}

@app.post("/webhooks/{symbol}", tags=["Webhooks"]) 
async def set_symbol_webhook(symbol: str, request: WebhookUpdateRequest):
    """Set or update webhook URL for a symbol"""
    symbol_upper = symbol.upper()
    
    webhook_url = request.webhook_url.strip()
    
    if not webhook_url:
        raise HTTPException(status_code=400, detail="webhook_url is required in request body")
    
    was_existing = webhook_manager.update_webhook(symbol_upper, webhook_url)
    
    if was_existing:
        logger.info(f"Updated webhook for {symbol_upper}")
        return {"status": "success", "message": f"Webhook updated for {symbol_upper}"}
    else:
        logger.info(f"Added new webhook for {symbol_upper}")
        return {"status": "success", "message": f"Webhook added for {symbol_upper}"}

@app.delete("/webhooks/{symbol}", tags=["Webhooks"]) 
async def delete_symbol_webhook(symbol: str):
    """Remove webhook for a symbol"""
    symbol_upper = symbol.upper()
    
    if symbol_upper == "DEFAULT":
        raise HTTPException(status_code=400, detail="Cannot delete default webhook")
    
    if webhook_manager.remove_webhook(symbol_upper):
        logger.info(f"Removed webhook for {symbol_upper}")
        return {"status": "success", "message": f"Webhook removed for {symbol_upper}"}
    else:
        raise HTTPException(status_code=404, detail=f"No webhook configured for {symbol_upper}")

@app.get("/symbols", tags=["Symbols"]) 
async def get_tracked_symbols():
    """Get list of all tracked symbols"""
    symbols = webhook_manager.get_all_symbols()
    return {
        "symbols": symbols,
        "total": len(symbols),
        "has_default": "default" in webhook_manager.webhooks
    }

@app.post("/symbols", tags=["Symbols"]) 
async def add_ticker(req: AddTickerRequest):
    """Add a ticker and set its webhook URL."""
    sym = req.symbol.upper()
    webhook_manager.set_webhook(sym, req.webhook_url)
    try:
        # Optionally prime symbol in state DB (best-effort)
        state_manager.ensure_symbol_exists(sym)
    except Exception as e:
        logger.debug(f"ensure_symbol_exists skipped: {e}")
    return {"status": "success", "symbol": sym}

@app.post("/admin/refresh-ema-states", tags=["Admin"]) 
async def refresh_ema_states(req: RefreshStatesRequest):
    """Pull candles via yfinance and update EMA direction per symbol/timeframe."""
    try:
        import time
        import yfinance as yf
        import pandas as pd
        import requests as rq

        # Map app TF to Yahoo base interval; resample where needed (e.g., 4HR)
        BASE_INTERVAL = {"5MIN":"5m","15MIN":"15m","30MIN":"30m","1HR":"60m","4HR":"60m","1DAY":"1d"}

        def effective_period_days(tf: str, requested_days: int) -> int:
            # Yahoo intraday limits: up to 60d for <=60m intervals
            return min(requested_days, 60) if tf in ("5MIN","15MIN","30MIN","1HR","4HR") else requested_days

        # Shared session with headers reduces JSONDecode issues/rate-limits
        session = rq.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TradeAlerts/2.0",
            "Accept": "application/json, text/javascript, */*; q=0.01"
        })

        def fetch_history(sym: str, tf: str, days: int, retries: int = 2) -> pd.DataFrame:
            interval = BASE_INTERVAL.get(tf.upper(), "60m")
            period_days = effective_period_days(tf.upper(), days)
            for attempt in range(retries + 1):
                try:
                    df = yf.download(
                        sym,
                        period=f"{period_days}d",
                        interval=interval,
                        progress=False,
                        auto_adjust=False,
                        threads=False,
                        session=session
                    )
                    if df is not None and not df.empty:
                        return df
                except Exception as e:
                    # fallback: try Ticker().history on next loop
                    time.sleep(0.8 * (attempt + 1))
                try:
                    hist = yf.Ticker(sym, session=session).history(period=f"{period_days}d", interval=interval, actions=False, auto_adjust=False, prepost=False)
                    if hist is not None and not hist.empty:
                        return hist
                except Exception:
                    pass
            return pd.DataFrame()

        def to_timeframe(df: pd.DataFrame, tf: str) -> pd.DataFrame:
            tfu = tf.upper()
            if tfu == "4HR":
                # Resample 60m to 4H
                if not isinstance(df.index, pd.DatetimeIndex):
                    df.index = pd.to_datetime(df.index)
                # Build robust agg dict for common Yahoo columns
                agg = {}
                if "Open" in df.columns:
                    agg["Open"] = "first"
                if "High" in df.columns:
                    agg["High"] = "max"
                if "Low" in df.columns:
                    agg["Low"] = "min"
                if "Close" in df.columns:
                    agg["Close"] = "last"
                if "Adj Close" in df.columns:
                    agg["Adj Close"] = "last"
                if "Volume" in df.columns:
                    agg["Volume"] = "sum"
                # Default any other columns to last
                for col in df.columns:
                    if col not in agg:
                        agg[col] = "last"
                res = df.resample("4h").agg(agg).dropna(how="all")
                return res
            return df

        import re
        def normalize_symbol(sym: str) -> str:
            cleaned = sym.strip().lstrip('$')
            cleaned = re.sub(r"[^A-Za-z0-9\.\-_=^]", "", cleaned)
            return cleaned.upper()

        raw_syms = (req.symbols or webhook_manager.get_all_symbols() or ["SPY"])
        symbols = [normalize_symbol(s) for s in raw_syms if normalize_symbol(s)]
        updated: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []
        for sym in symbols:
            for tf in req.timeframes:
                try:
                    raw = fetch_history(sym, tf, req.lookback_days)
                except Exception as e:
                    msg = f"Download failed for {sym} {tf}: {e}"
                    logger.warning(msg)
                    errors.append({"symbol": sym, "timeframe": tf, "error": str(e)})
                    continue
                data = to_timeframe(raw, tf)
                if data is None or getattr(data, 'empty', True) or 'Close' not in data:
                    logger.warning(f"No data for {sym} {tf}")
                    errors.append({"symbol": sym, "timeframe": tf, "error": "no_data"})
                    continue
                close = data['Close'].dropna()
                if close.empty:
                    logger.warning(f"No close data for {sym} {tf}")
                    errors.append({"symbol": sym, "timeframe": tf, "error": "no_close"})
                    continue
                price = float(close.iloc[-1])
                for pair in req.ema_pairs:
                    if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                        continue
                    short, long = int(pair[0]), int(pair[1])
                    ema_s = close.ewm(span=short, adjust=False).mean()
                    ema_l = close.ewm(span=long, adjust=False).mean()
                    direction = "bullish" if float(ema_s.iloc[-1]) > float(ema_l.iloc[-1]) else "bearish"
                    state_manager.update_timeframe_state(sym, tf, "ema", direction, price)
                    updated.append({"symbol": sym, "timeframe": tf, "ema_pair": f"{short}/{long}", "direction": direction.upper(), "price": price})
        status = "success" if updated and not errors else ("partial" if updated else "error")
        return {"status": status, "updated": updated, "errors": errors}
    except Exception as e:
        logger.error(f"refresh_ema_states failed: {e}")
        return {"status": "error", "updated": [], "errors": [{"error": str(e)}]}

# Alerts toggle endpoints
@app.get("/alerts/{symbol}", tags=["Alerts"], include_in_schema=False) 
async def get_alert_toggles(symbol: str):
    """Return per-ticker alert tag toggles, e.g., C1, CALL1, P1, PUT1, etc."""
    sym = symbol.upper()
    alert_toggle_manager.ensure_defaults(sym)
    return {"symbol": sym, "toggles": alert_toggle_manager.get(sym)}

@app.post("/alerts/{symbol}", tags=["Alerts"], include_in_schema=False) 
async def set_alert_toggles(symbol: str, toggles: Dict[str, bool] = Body(...)):
    """Set multiple toggles at once. Body: { "C1": true, "CALL1": false, ... }"""
    sym = symbol.upper()
    alert_toggle_manager.ensure_defaults(sym)
    updated = alert_toggle_manager.set_many(sym, toggles or {})
    return {"symbol": sym, "toggles": updated}

@app.get("/admin/alerts", include_in_schema=False)
async def admin_alerts_page():
    html = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Per-Ticker Alert Toggles</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin: 20px; }
    .row { display: flex; align-items: center; margin: 6px 0; flex-wrap: wrap; }
    .sym { font-weight: 600; width: 80px; }
    .tag { margin: 2px 8px 2px 0; }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(90px, 1fr)); gap: 6px; }
    .card { border: 1px solid #ddd; border-radius: 8px; padding: 12px; margin: 10px 0; }
    button { padding: 6px 10px; margin-left: 8px; }
    input[type="text"] { padding: 6px 8px; }
    .muted { color: #666; font-size: 12px; }
  </style>
  </head>
<body>
  <h2>Per-Ticker Alert Toggles</h2>
  <div class="row">
    <input id="newSym" type="text" placeholder="Add symbol (e.g., QQQ)" />
    <button onclick="addSymbol()">Add</button>
    <span class="muted">Symbols come from your webhook config; this also primes defaults.</span>
  </div>
  <div id="container"></div>

<script>
async function listSymbols() {
  const r = await fetch('/symbols');
  const j = await r.json();
  const syms = (j.symbols || []);
  if (!syms.includes('SPY')) syms.unshift('SPY');
  return Array.from(new Set(syms));
}

function chunkTags(keys) {
  const order = ["C","CALL","P","PUT"];
  const tfs = ["1","5","15","30","1H","2H","4H","1D"];
  const wanted = [];
  for (const b of order) for (const tf of tfs) wanted.push((b+tf).toUpperCase());
  const seen = new Set();
  const result = [];
  for (const k of wanted) { if (keys.includes(k) && !seen.has(k)) { result.push(k); seen.add(k);} }
  for (const k of keys) if (!seen.has(k)) result.push(k);
  return result;
}

async function load() {
  const container = document.getElementById('container');
  container.innerHTML = '';
  const symbols = await listSymbols();
  for (const sym of symbols) {
    const res = await fetch(`/alerts/${sym}`);
    const data = await res.json();
    const toggles = data.toggles || {};
    const keys = chunkTags(Object.keys(toggles));
    const card = document.createElement('div');
    card.className = 'card';
    card.innerHTML = `
      <div class="row"><div class="sym">${sym}</div>
        <button onclick="save('${sym}')">Save</button>
      </div>
      <div class="grid" id="grid-${sym}"></div>
    `;
    container.appendChild(card);
    const grid = card.querySelector(`#grid-${sym}`);
    for (const k of keys) {
      const checked = !!toggles[k];
      const id = `${sym}-${k}`;
      const div = document.createElement('div');
      div.className = 'tag';
      div.innerHTML = `
        <label><input type="checkbox" id="${id}" ${checked ? 'checked' : ''} /> ${k} </label>
      `;
      grid.appendChild(div);
    }
  }
}

async function save(sym) {
  const grid = document.getElementById(`grid-${sym}`);
  if (!grid) return;
  const inputs = grid.querySelectorAll('input[type="checkbox"]');
  const body = {};
  inputs.forEach(i => { 
    const k = i.id.replace(`${sym}-`, '');
    body[k] = i.checked;
  });
  await fetch(`/alerts/${sym}`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  });
  alert(`Saved toggles for ${sym}`);
}

async function addSymbol() {
  const el = document.getElementById('newSym');
  const sym = (el.value || '').trim().toUpperCase();
  if (!sym) return;
  await fetch(`/alerts/${sym}`);
  el.value = '';
  load();
}

load();
</script>
</body>
</html>
    """
    return HTMLResponse(content=html, status_code=200)

@app.get("/debug/states", tags=["Debug"]) 
async def debug_states(symbol: str = "SPY", all_symbols: bool = False) -> Dict[str, Any]:
    """
    Inspect current timeframe states from the database.
    - symbol: symbol to inspect (default: SPY)
    - all_symbols: if true, returns summaries for all configured symbols
    """
    try:
        if all_symbols:
            symbols_list: List[str] = webhook_manager.get_all_symbols()
            if "SPY" not in symbols_list:
                symbols_list.append("SPY")
            out: Dict[str, Any] = {}
            for s in sorted(set([x.upper() for x in symbols_list])):
                out[s] = state_manager.get_state_summary(s)
            return {"mode": "all_symbols", "count": len(out), "data": out}
        else:
            s = symbol.upper()
            summary = state_manager.get_state_summary(s)
            return {"mode": "single", "symbol": s, "data": summary}
    except Exception as e:
        logger.error(f"Failed to collect state summaries: {e}")
        return {"error": str(e)}

if __name__ == "__main__":
    import uvicorn
    import os
    
    # Production startup
    logger.info("=" * 60)
    logger.info("STARTING TRADE ALERTS SYSTEM v2.0")
    logger.info(f"Port: {PRODUCTION_PORT}")
    logger.info(f"Database: {PRODUCTION_DATABASE}")
    logger.info(f"Log File: {PRODUCTION_LOG_FILE}")
    logger.info("=" * 60)
    
    # Use production port, fallback to environment variable
    port = int(os.environ.get("PORT", PRODUCTION_PORT))
    uvicorn.run(app, host="0.0.0.0", port=port)
