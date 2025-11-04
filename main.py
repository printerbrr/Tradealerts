import os
import re
from fastapi import FastAPI, HTTPException, Request, Body
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import logging
from datetime import datetime
import json
import hashlib
import hmac
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

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

class PriceAlertMessage(BaseModel):
    """Data model for incoming price alert messages"""
    message: str
    sender: Optional[str] = None
    timestamp: Optional[str] = None

class PriceAlertWebhookRequest(BaseModel):
    """Request model for setting price alert webhook URL"""
    webhook_url: str

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

# Price Alert Webhook Configuration (separate from regular alerts)
# Load from environment variable first, then from webhook manager, then from config file
PRICE_ALERT_WEBHOOK_URL = os.environ.get("PRICE_ALERT_WEBHOOK_URL")

# If not in environment, try to load from webhook manager
if not PRICE_ALERT_WEBHOOK_URL:
    PRICE_ALERT_WEBHOOK_URL = webhook_manager.get_price_alert_webhook()

# If still not found, try loading from config file (persistent across redeploys)
if not PRICE_ALERT_WEBHOOK_URL:
    try:
        price_alert_config_file = "price_alert_webhook.txt"
        if os.path.exists(price_alert_config_file):
            with open(price_alert_config_file, "r") as f:
                PRICE_ALERT_WEBHOOK_URL = f.read().strip()
                if PRICE_ALERT_WEBHOOK_URL:
                    logger.info(f"Price alert webhook URL loaded from config file: {PRICE_ALERT_WEBHOOK_URL[:50]}...")
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning(f"Failed to load price alert webhook from config file: {e}")

if PRICE_ALERT_WEBHOOK_URL:
    logger.info(f"Price alert webhook URL loaded: {PRICE_ALERT_WEBHOOK_URL[:50]}...")
else:
    logger.warning("PRICE_ALERT_WEBHOOK_URL not found - price alerts will be disabled until configured")

# Dev Mode Webhook Configuration
DEV_MODE_WEBHOOK_URL = os.environ.get("DEV_MODE_WEBHOOK_URL")

# If not in environment, try loading from config file
if not DEV_MODE_WEBHOOK_URL:
    try:
        dev_webhook_config_file = "dev_mode_webhook.txt"
        if os.path.exists(dev_webhook_config_file):
            with open(dev_webhook_config_file, "r") as f:
                DEV_MODE_WEBHOOK_URL = f.read().strip()
                if DEV_MODE_WEBHOOK_URL:
                    logger.info(f"Dev mode webhook URL loaded from config file: {DEV_MODE_WEBHOOK_URL[:50]}...")
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning(f"Failed to load dev mode webhook from config file: {e}")

if DEV_MODE_WEBHOOK_URL:
    logger.info(f"Dev mode webhook URL loaded: {DEV_MODE_WEBHOOK_URL[:50]}...")
else:
    logger.info("DEV_MODE_WEBHOOK_URL not configured - dev mode will use production webhooks if enabled")

# Discord Bot Configuration (for slash commands)
DISCORD_BOT_PUBLIC_KEY = os.environ.get("DISCORD_BOT_PUBLIC_KEY")
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")

if DISCORD_BOT_PUBLIC_KEY:
    logger.info("Discord bot public key loaded (slash commands enabled)")
else:
    logger.info("DISCORD_BOT_PUBLIC_KEY not configured - slash commands will be disabled")

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

# Discord Bot Interaction Handlers
def verify_discord_signature(body: bytes, signature: str, timestamp: str) -> bool:
    """
    Verify Discord interaction signature using Ed25519
    """
    try:
        from nacl.signing import VerifyKey
        from nacl.exceptions import BadSignatureError
        
        if not DISCORD_BOT_PUBLIC_KEY:
            logger.warning("Discord bot public key not configured - cannot verify signature")
            return False
        
        # Reconstruct the message that was signed
        message = timestamp.encode() + body
        
        # Convert hex public key to bytes
        public_key_bytes = bytes.fromhex(DISCORD_BOT_PUBLIC_KEY)
        
        # Create verify key
        verify_key = VerifyKey(public_key_bytes)
        
        # Convert hex signature to bytes
        signature_bytes = bytes.fromhex(signature)
        
        # Verify signature
        verify_key.verify(message, signature_bytes)
        return True
        
    except BadSignatureError:
        logger.warning("Discord signature verification failed")
        return False
    except Exception as e:
        logger.error(f"Error verifying Discord signature: {e}")
        return False

async def handle_discord_command(command_name: str, options: Dict[str, Any]) -> Dict[str, Any]:
    """
    Route Discord slash commands to appropriate handlers
    Returns response data for Discord
    """
    try:
        if command_name == "dev-mode":
            # Extract enabled option
            enabled = False
            for option in options:
                if option.get("name") == "enabled":
                    enabled = option.get("value", False)
                    break
            
            # Set dev mode
            alert_config.parameters["dev_mode"] = enabled
            
            # Set ignore filters based on dev mode state
            if enabled:
                alert_config.parameters["ignore_time_filter"] = True
                alert_config.parameters["ignore_weekend_filter"] = True
                message = "Dev mode enabled ✅\n- Using dev webhook\n- Time/weekend filters bypassed"
            else:
                # Re-enable filters when dev mode is disabled
                alert_config.parameters["ignore_time_filter"] = False
                alert_config.parameters["ignore_weekend_filter"] = False
                message = "Dev mode disabled ✅\n- Using production webhooks\n- Normal filters active"
            
            logger.info(f"Discord command: dev-mode set to {enabled}")
            
            return {
                "type": 4,  # CHANNEL_MESSAGE_WITH_SOURCE
                "data": {
                    "content": message
                }
            }
        
        elif command_name == "test-mode":
            # Enable test mode (disables both filters)
            alert_config.parameters["ignore_time_filter"] = True
            alert_config.parameters["ignore_weekend_filter"] = True
            logger.info("Discord command: test-mode enabled")
            
            return {
                "type": 4,
                "data": {
                    "content": "Test mode enabled ✅\n- Time/weekend filters disabled"
                }
            }
        
        elif command_name == "status":
            # Return current status
            dev_mode = alert_config.parameters.get("dev_mode", False)
            time_filter = not alert_config.parameters.get("ignore_time_filter", False)
            weekend_filter = not alert_config.parameters.get("ignore_weekend_filter", False)
            
            status_msg = f"""**System Status:**
• Dev Mode: {'🟢 ON' if dev_mode else '🔴 OFF'}
• Time Filter: {'🟢 ON' if time_filter else '🔴 OFF'} (5am-1pm PT)
• Weekend Filter: {'🟢 ON' if weekend_filter else '🔴 OFF'}"""
            
            return {
                "type": 4,
                "data": {
                    "content": status_msg
                }
            }
        
        elif command_name == "ema-summary":
            # Trigger the existing EMA summary endpoint
            try:
                await send_daily_ema_summaries()
                logger.info("Discord command: ema-summary triggered - summaries sent to webhooks")
                
                symbols = webhook_manager.get_all_symbols()
                if not symbols:
                    symbols = ["SPY"]
                
                return {
                    "type": 4,
                    "data": {
                        "content": f"✅ EMA summaries sent to {len(symbols)} configured webhook channel(s)"
                    }
                }
            except Exception as e:
                logger.error(f"Error sending EMA summaries via Discord command: {e}")
                return {
                    "type": 4,
                    "data": {
                        "content": f"❌ Error sending EMA summaries: {str(e)}"
                    }
                }
        
        else:
            return {
                "type": 4,
                "data": {
                    "content": f"❌ Unknown command: {command_name}"
                }
            }
            
    except Exception as e:
        logger.error(f"Error handling Discord command {command_name}: {e}")
        return {
            "type": 4,
            "data": {
                "content": f"❌ Error processing command: {str(e)}"
            }
        }

@app.post("/discord/interactions", tags=["Discord"])
async def discord_interactions(request: Request):
    """
    Discord interaction webhook endpoint for slash commands
    Handles signature verification and routes commands
    """
    try:
        # Get raw body for signature verification
        body = await request.body()
        body_str = body.decode('utf-8')
        
        # Get signature headers
        signature = request.headers.get("X-Signature-Ed25519", "")
        timestamp = request.headers.get("X-Signature-Timestamp", "")
        
        # Log incoming request for debugging
        logger.info(f"Discord interaction received - signature present: {bool(signature)}, timestamp present: {bool(timestamp)}")
        
        # Verify signature if public key is configured
        if DISCORD_BOT_PUBLIC_KEY:
            if not verify_discord_signature(body, signature, timestamp):
                logger.warning(f"Discord interaction signature verification failed - signature: {signature[:20]}..., timestamp: {timestamp}")
                raise HTTPException(status_code=401, detail="Invalid signature")
        else:
            logger.warning("DISCORD_BOT_PUBLIC_KEY not configured - signature verification disabled")
        
        # Parse interaction payload
        try:
            interaction = json.loads(body_str)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Discord interaction JSON: {e}")
            logger.error(f"Body received: {body_str[:200]}")
            raise HTTPException(status_code=400, detail="Invalid JSON")
        
        # Handle PING (Discord verification) - must respond within 3 seconds
        if interaction.get("type") == 1:
            logger.info("Discord PING received - responding with PONG")
            return {"type": 1}
        
        # Handle APPLICATION_COMMAND (slash command)
        if interaction.get("type") == 2:
            data = interaction.get("data", {})
            command_name = data.get("name", "")
            options = data.get("options", [])
            
            logger.info(f"Discord command received: {command_name} with options: {options}")
            
            # Route command to handler
            response = await handle_discord_command(command_name, options)
            return response
        
        # Unknown interaction type
        logger.warning(f"Unknown Discord interaction type: {interaction.get('type')}")
        return {
            "type": 4,
            "data": {
                "content": "❌ Unknown interaction type"
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing Discord interaction: {e}")
        raise HTTPException(status_code=500, detail=str(e))

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
        
        # Check if this is a price alert (bypass time/weekend filters)
        message_lower = message.lower()
        is_price_alert = (
            "mark is at or above" in message_lower or 
            "mark is at or below" in message_lower
        )
        
        if is_price_alert:
            # Route to price alert handler (bypasses time/weekend filters)
            logger.info("Detected price alert in SMS - routing to price alert handler")
            parsed_data = parse_price_alert(message)
            
            # Send to Discord using price alert webhook
            success = await send_price_alert_to_discord(parsed_data)
            
            if success:
                return {"status": "success", "message": "Price alert processed and sent to Discord"}
            else:
                return {"status": "error", "message": "Price alert processed but failed to send to Discord"}
        
        # Parse the SMS data for regular alerts
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
            else:
                # Log skipped alerts that don't match known categories
                logger.info(f"ALERT SKIPPED: Alert not categorized into known alert types. Parsed data: {json.dumps(parsed_data, indent=2)}")
        
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
        # Handle trailing periods or punctuation that might follow the number
        price_match = re.search(r'MARK\s*=\s*(\d+(?:\.\d+)?)', message, re.IGNORECASE)
        if price_match:
            price_value = price_match.group(1)
            # Strip any trailing periods that might have been captured
            price_value = price_value.rstrip('.')
            parsed["price"] = float(price_value)
        
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
        # Handle trailing periods or punctuation
        study_match = re.search(r'STUDY\s*=\s*(\d+(?:\.\d+)?)', message, re.IGNORECASE)
        if study_match:
            study_value = study_match.group(1)
            # Strip any trailing periods that might have been captured
            study_value = study_value.rstrip('.')
            parsed["study_details"] = study_value
        
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
            r'\$(\d+(?:\.\d+)?)',      # $150.50
            r'at \$(\d+(?:\.\d+)?)',   # at $150.50
            r'price.*?(\d+(?:\.\d+)?)', # price 150.50
            r'(\d+(?:\.\d+)?)\s*\$'    # 150.50 $
        ]
        
        for pattern in price_patterns:
            price_match = re.search(pattern, message, re.IGNORECASE)
            if price_match:
                price_value = price_match.group(1)
                # Strip any trailing periods that might have been captured
                price_value = price_value.rstrip('.')
                parsed["price"] = float(price_value)
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
    
    # Only allow specific categorized alerts: MACD and EMA crossovers
    # Primary focus: MACD Crossover detection
    if parsed_data.get("action") == "macd_crossover":
        logger.info("MACD CROSSOVER DETECTED! Triggering Discord alert")
        return True
    
    # Secondary focus: Moving Average Crossover detection
    if parsed_data.get("action") == "moving_average_crossover":
        logger.info("EMA CROSSOVER DETECTED! Triggering Discord alert")
        return True
    
    # All other alerts are skipped (not categorized into known alert types)
    # This includes:
    # - High-confidence Schwab alerts that aren't MACD/EMA crossovers
    # - Trade signals that aren't MACD/EMA crossovers
    # - Testing alerts (e.g., HOOKTRADESRVOL2)
    # - Any other uncategorized alerts
    logger.info(f"ALERT NOT CATEGORIZED: action={parsed_data.get('action')}, alert_type={parsed_data.get('alert_type')}, confidence={parsed_data.get('confidence')}")
    return False

async def send_discord_alert(log_data: Dict[str, Any]):
    """
    Send alert to Discord webhook based on symbol
    """
    try:
        import requests
        
        parsed = log_data['parsed_data']
        symbol = parsed.get('symbol', 'SPY').upper()
        
        # Check if dev mode is enabled - if so, use dev webhook
        if alert_config.parameters.get('dev_mode', False):
            webhook_url = DEV_MODE_WEBHOOK_URL
            if not webhook_url:
                logger.warning("Dev mode enabled but DEV_MODE_WEBHOOK_URL not configured - falling back to production webhook")
                webhook_url = webhook_manager.get_webhook(symbol)
            else:
                logger.info(f"DEV MODE: Using dev webhook for {symbol}")
        else:
            # Get webhook URL for this symbol (production mode)
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
            
        # Helper function to determine number of emojis based on timeframe
        def get_emoji_count(timeframe: str) -> int:
            """Return number of emojis based on timeframe:
            1min, 5min: 1 emoji
            15min, 30min: 2 emojis
            1h, 2h: 3 emojis
            4h, D: 4 emojis
            """
            if not timeframe:
                return 1
            tf = timeframe.upper()
            if tf in ['1MIN', '5MIN']:
                return 1
            elif tf in ['15MIN', '30MIN']:
                return 2
            elif tf in ['1HR', '2HR']:
                return 3
            elif tf in ['4HR', '1DAY', '4H', '1D']:
                return 4
            # Default to 1 for unknown timeframes
            return 1
        
        # Helper function to get emoji string based on direction and timeframe
        def get_emoji_string(direction: str, timeframe: str) -> str:
            """Get emoji string with correct count based on timeframe"""
            is_bullish = direction.lower() == 'bullish'
            emoji_char = '🟢' if is_bullish else '🔴'
            count = get_emoji_count(timeframe)
            return emoji_char * count
            
        # Create different message formats based on alert type
        if parsed.get('action') == 'macd_crossover':
            # MACD: custom compact format using next higher timeframe suffix
            macd_direction = (parsed.get('macd_direction', 'bullish') or 'bullish').lower()
            # Map direction to Call/Put
            direction_label = 'Call' if macd_direction == 'bullish' else 'Put'

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
            # Special case: 5MIN MACD should use 2 emojis (like 15MIN/30MIN)
            emoji_count = 2 if current_tf == '5MIN' else get_emoji_count(current_tf)
            emoji_char = '🟢' if macd_direction == 'bullish' else '🔴'
            emoji_str = emoji_char * emoji_count

            message = f"""{emoji_str}
{title_tf} MACD Cross - {direction_label}{suffix}
MARK: ${parsed.get('price', 'N/A')}
TIME: {display_time}
@everyone"""

            # Build toggle tag for MACD using CALL/PUT + timeframe token
            macd_dir_label = 'CALL' if macd_direction == 'bullish' else 'PUT'
            macd_suffix = (suffix or '').upper().replace('H', 'H').replace('D', 'D')
            toggle_tag = f"{macd_dir_label}{macd_suffix}"
        else:
            # EMA Crossover format using confluence with next higher timeframe
            current_tf = (parsed.get('timeframe') or '').upper()
            next_tf = state_manager.get_next_higher_timeframe(current_tf) if current_tf else None
            ema_direction = (parsed.get('ema_direction', 'bullish') or 'bullish').lower()

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
            emoji_str = get_emoji_string(ema_direction, current_tf)
            message = f"""{emoji_str}
{title_tf} EMA Cross - {tag}
MARK: ${parsed.get('price', 'N/A')}
TIME: {display_time}
@everyone"""

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
    
    # Check time filter if not in dev mode (dev mode bypasses filters)
    if not alert_config.parameters.get('dev_mode', False):
        # Check if time filter is enabled and we're outside allowed hours
        if not alert_config.parameters.get('ignore_time_filter', False):
            import pytz
            from datetime import datetime
            pacific = pytz.timezone('America/Los_Angeles')
            current_time_pacific = datetime.now(pacific)
            current_hour = current_time_pacific.hour
            
            # No alerts between 1 PM (13:00) and 4:59 AM (4:59)
            if 13 <= current_hour or current_hour < 5:
                logger.info(f"EMA SUMMARY FILTERED: Current time {current_time_pacific.strftime('%I:%M %p')} is outside alert hours (5 AM - 1 PM PST/PDT)")
                return
    
    # Check if dev mode is enabled - if so, use dev webhook for all summaries
    if alert_config.parameters.get('dev_mode', False):
        webhook_url = DEV_MODE_WEBHOOK_URL
        if not webhook_url:
            logger.warning("Dev mode enabled but DEV_MODE_WEBHOOK_URL not configured - falling back to production webhooks")
            webhook_url = None  # Will use production webhooks per symbol below
        else:
            logger.info(f"DEV MODE: Using dev webhook for EMA summaries")
            # Send combined summary to dev webhook
            summary_lines = []
            for sym in symbols:
                summary_text = _build_ema_summary(sym)
                summary_lines.append(f"**{sym} EMA States**\n{summary_text}")
            
            if summary_lines:
                combined_content = "\n\n".join(summary_lines)
                ok = _post_discord_message(webhook_url, combined_content)
                if ok:
                    logger.info(f"Daily EMA summary sent to dev webhook for {len(symbols)} symbol(s)")
                else:
                    logger.warning(f"Failed to send daily EMA summary to dev webhook")
            return
    
    # Production mode - send to each symbol's webhook
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

@app.post("/config/test-mode", tags=["Config"], include_in_schema=False)
async def enable_test_mode():
    """One-click test mode: disables both time filter and weekend filter for testing."""
    alert_config.parameters["ignore_time_filter"] = True
    alert_config.parameters["ignore_weekend_filter"] = True
    logger.info("Test mode enabled: both time filter and weekend filter disabled")
    return {
        "status": "success",
        "message": "Test mode enabled",
        "time_filter_disabled": True,
        "weekend_filter_disabled": True
    }

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

@app.post("/admin/refresh-ema-states", tags=["Admin"], include_in_schema=False) 
async def refresh_ema_states(req: RefreshStatesRequest):
    """This endpoint is disabled - yfinance and finnhub are no longer supported."""
    logger.warning("refresh_ema_states endpoint called but is disabled (yfinance/finnhub removed)")
    raise HTTPException(
        status_code=503, 
        detail="This endpoint is disabled. yfinance and finnhub data fetching have been removed. EMA states are only updated via incoming SMS alerts."
    )

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
    .card { border: 1px solid #ddd; border-radius: 8px; padding: 12px; margin: 10px 0; }
    button { padding: 6px 10px; margin-left: 8px; }
    input[type="text"] { padding: 6px 8px; }
    .muted { color: #666; font-size: 12px; }
    .columns-container { display: flex; gap: 20px; margin-top: 10px; }
    .column { flex: 1; border: 1px solid #eee; border-radius: 4px; padding: 10px; }
    .column-header { font-weight: 600; font-size: 14px; margin-bottom: 10px; text-align: center; padding-bottom: 8px; border-bottom: 1px solid #ddd; }
    .column-content { display: flex; flex-direction: column; gap: 4px; }
    .checkbox-item { display: flex; align-items: center; padding: 4px 0; }
    .checkbox-item label { display: flex; align-items: center; cursor: pointer; width: 100%; }
    .checkbox-item input[type="checkbox"] { margin-right: 6px; }
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

function organizeTags(toggles) {
  // Timeframes sorted from low to high
  const tfs = ["1", "5", "15", "30", "1H", "2H", "4H", "1D"];
  
  // Organize into three columns: C/P, CALL/PUT, Call/Put
  const column1 = []; // C/P
  const column2 = []; // CALL/PUT
  const column3 = []; // Call/Put
  
  for (const tf of tfs) {
    // Column 1: C/P
    const cKey = `C${tf}`;
    const pKey = `P${tf}`;
    if (toggles.hasOwnProperty(cKey)) {
      column1.push({ key: cKey, checked: !!toggles[cKey] });
    }
    if (toggles.hasOwnProperty(pKey)) {
      column1.push({ key: pKey, checked: !!toggles[pKey] });
    }
    
    // Column 2: CALL/PUT
    const callKey = `CALL${tf}`;
    const putKey = `PUT${tf}`;
    if (toggles.hasOwnProperty(callKey)) {
      column2.push({ key: callKey, checked: !!toggles[callKey] });
    }
    if (toggles.hasOwnProperty(putKey)) {
      column2.push({ key: putKey, checked: !!toggles[putKey] });
    }
    
    // Column 3: Call/Put
    const callKeyMixed = `Call${tf}`;
    const putKeyMixed = `Put${tf}`;
    if (toggles.hasOwnProperty(callKeyMixed)) {
      column3.push({ key: callKeyMixed, checked: !!toggles[callKeyMixed] });
    }
    if (toggles.hasOwnProperty(putKeyMixed)) {
      column3.push({ key: putKeyMixed, checked: !!toggles[putKeyMixed] });
    }
  }
  
  return { column1, column2, column3 };
}

async function load() {
  const container = document.getElementById('container');
  container.innerHTML = '';
  const symbols = await listSymbols();
  for (const sym of symbols) {
    const res = await fetch(`/alerts/${sym}`);
    const data = await res.json();
    const toggles = data.toggles || {};
    const { column1, column2, column3 } = organizeTags(toggles);
    
    const card = document.createElement('div');
    card.className = 'card';
    card.innerHTML = `
      <div class="row"><div class="sym">${sym}</div>
        <button onclick="save('${sym}')">Save</button>
      </div>
      <div class="columns-container" id="columns-${sym}"></div>
    `;
    container.appendChild(card);
    
    const columnsContainer = card.querySelector(`#columns-${sym}`);
    
    // Column 1: C/P
    const col1 = document.createElement('div');
    col1.className = 'column';
    col1.innerHTML = '<div class="column-header">C/P</div><div class="column-content" id="col1-' + sym + '"></div>';
    const col1Content = col1.querySelector('#col1-' + sym);
    for (const item of column1) {
      const id = `${sym}-${item.key}`;
      const div = document.createElement('div');
      div.className = 'checkbox-item';
      div.innerHTML = `
        <label><input type="checkbox" id="${id}" ${item.checked ? 'checked' : ''} /> ${item.key}</label>
      `;
      col1Content.appendChild(div);
    }
    columnsContainer.appendChild(col1);
    
    // Column 2: CALL/PUT
    const col2 = document.createElement('div');
    col2.className = 'column';
    col2.innerHTML = '<div class="column-header">CALL/PUT</div><div class="column-content" id="col2-' + sym + '"></div>';
    const col2Content = col2.querySelector('#col2-' + sym);
    for (const item of column2) {
      const id = `${sym}-${item.key}`;
      const div = document.createElement('div');
      div.className = 'checkbox-item';
      div.innerHTML = `
        <label><input type="checkbox" id="${id}" ${item.checked ? 'checked' : ''} /> ${item.key}</label>
      `;
      col2Content.appendChild(div);
    }
    columnsContainer.appendChild(col2);
    
    // Column 3: Call/Put
    const col3 = document.createElement('div');
    col3.className = 'column';
    col3.innerHTML = '<div class="column-header">Call/Put</div><div class="column-content" id="col3-' + sym + '"></div>';
    const col3Content = col3.querySelector('#col3-' + sym);
    for (const item of column3) {
      const id = `${sym}-${item.key}`;
      const div = document.createElement('div');
      div.className = 'checkbox-item';
      div.innerHTML = `
        <label><input type="checkbox" id="${id}" ${item.checked ? 'checked' : ''} /> ${item.key}</label>
      `;
      col3Content.appendChild(div);
    }
    columnsContainer.appendChild(col3);
  }
}

async function save(sym) {
  const columnsContainer = document.getElementById(`columns-${sym}`);
  if (!columnsContainer) return;
  const inputs = columnsContainer.querySelectorAll('input[type="checkbox"]');
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

# ============================================================================
# PRICE ALERT FRAMEWORK
# ============================================================================

def parse_price_alert(message: str) -> Dict[str, Any]:
    """
    Parse incoming Schwab price alert message.
    
    Expected format: "SPY mark is at or above $682.58 Mark = 683.32"
    or: "SPY mark is at or below $682.58 Mark = 683.32"
    
    Args:
        message: Raw price alert message text
        
    Returns:
        Dictionary with parsed price alert data:
        - symbol: Stock symbol (e.g., "SPY", "QQQ")
        - direction: "AT OR ABOVE" or "AT OR BELOW"
        - alert_level: Alert price level (e.g., "$682.58")
        - mark: Current mark price (e.g., "683.32")
    """
    parsed = {
        "raw_message": message,
        "symbol": None,
        "direction": None,
        "alert_level": None,
        "mark": None,
    }
    
    # Extract symbol (1-5 uppercase letters before "mark")
    symbol_match = re.search(r'\b([A-Z]{1,5})\s+mark\s+is', message, re.IGNORECASE)
    if symbol_match:
        parsed["symbol"] = symbol_match.group(1).upper()
    
    # Extract direction: "at or above" or "at or below"
    direction_match = re.search(r'at or (above|below)', message, re.IGNORECASE)
    if direction_match:
        direction_raw = direction_match.group(1).upper()
        parsed["direction"] = f"AT OR {direction_raw}"
    
    # Extract alert level: $ followed by digits with optional decimal
    # Handle trailing periods or punctuation
    alert_level_match = re.search(r'\$(\d+(?:\.\d+)?)', message)
    if alert_level_match:
        alert_value = alert_level_match.group(1)
        # Strip any trailing periods that might have been captured
        alert_value = alert_value.rstrip('.')
        parsed["alert_level"] = f"${alert_value}"
    
    # Extract mark price: "Mark = " followed by digits with optional decimal
    # Handle trailing periods or punctuation that might follow the number
    # Pattern matches number up to whitespace, punctuation, or end of string
    mark_match = re.search(r'Mark\s*=\s*(\d+(?:\.\d+)?)', message, re.IGNORECASE)
    if mark_match:
        mark_value = mark_match.group(1)
        # Strip any trailing periods that might have been captured
        mark_value = mark_value.rstrip('.')
        parsed["mark"] = mark_value
    
    logger.info(f"Parsed price alert: {parsed}")
    return parsed

def format_price_alert_discord(parsed_data: Dict[str, Any]) -> str:
    """
    Format parsed price alert data into Discord message format.
    
    Format: "{TICKER} is {AT OR ABOVE/AT OR BELOW} {ALERT LEVEL} 
    
    MARK: ${MARK}
    @everyone"
    
    Args:
        parsed_data: Dictionary with parsed price alert data
        
    Returns:
        Formatted Discord message string
    """
    symbol = parsed_data.get("symbol", "N/A")
    direction = parsed_data.get("direction", "N/A")
    alert_level = parsed_data.get("alert_level", "N/A")
    mark = parsed_data.get("mark", "N/A")
    
    formatted_message = f"""{symbol} is {direction} {alert_level}

MARK: ${mark}
@everyone"""
    
    logger.info(f"Formatted price alert message: {formatted_message[:100]}...")
    return formatted_message

async def send_price_alert_to_discord(parsed_data: Dict[str, Any]) -> bool:
    """
    Send price alert to Discord using the separate price alert webhook.
    
    Args:
        parsed_data: Dictionary with parsed price alert data
        
    Returns:
        True if sent successfully, False otherwise
    """
    try:
        import requests
        
        # Check both global variable and webhook manager (in case it was updated)
        webhook_url = PRICE_ALERT_WEBHOOK_URL or webhook_manager.get_price_alert_webhook()
        
        if not webhook_url:
            logger.warning("Price alert webhook URL not configured - cannot send price alert")
            return False
        
        # Format the alert message
        formatted_message = format_price_alert_discord(parsed_data)
        
        # Send to Discord
        payload = {
            "content": formatted_message
        }
        
        response = requests.post(
            webhook_url,
            json=payload,
            headers={"Content-Type": "application/json"}
        )
        
        if response.status_code == 204:
            logger.info(f"Price alert sent to Discord successfully")
            return True
        else:
            error_msg = f"Failed to send price alert: {response.status_code}"
            try:
                response_text = response.text
                if response_text:
                    error_msg += f" - Response: {response_text[:200]}"
            except:
                pass
            logger.error(error_msg)
            return False
            
    except Exception as e:
        logger.error(f"Error sending price alert to Discord: {str(e)}")
        return False

@app.post("/webhook/price-alert", tags=["Ingest"])
async def receive_price_alert(alert: PriceAlertMessage):
    """
    Webhook endpoint to receive price alert messages.
    
    This endpoint receives price alerts, parses them, and sends formatted
    messages to a separate Discord webhook.
    """
    try:
        message = alert.message
        sender = alert.sender or "unknown"
        timestamp = alert.timestamp or datetime.now().isoformat()
        
        logger.info(f"Received price alert from {sender}: {message[:100]}...")
        
        # Parse the price alert message
        parsed_data = parse_price_alert(message)
        
        # Log the parsed data
        log_data = {
            "timestamp": timestamp,
            "sender": sender,
            "original_message": message,
            "parsed_data": parsed_data
        }
        
        logger.info(f"Parsed price alert data: {json.dumps(log_data, indent=2)}")
        
        # Send to Discord
        success = await send_price_alert_to_discord(parsed_data)
        
        if success:
            return {
                "status": "success",
                "message": "Price alert processed and sent to Discord"
            }
        else:
            return {
                "status": "error",
                "message": "Price alert processed but failed to send to Discord"
            }
        
    except Exception as e:
        logger.error(f"Error processing price alert: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/config/price-alert-webhook", tags=["Config"])
async def get_price_alert_webhook():
    """Get current price alert webhook URL configuration"""
    # Check both global variable and webhook manager (in case it was updated)
    webhook_url = PRICE_ALERT_WEBHOOK_URL or webhook_manager.get_price_alert_webhook()
    
    if webhook_url:
        masked_url = f"{webhook_url[:50]}..." if len(webhook_url) > 50 else webhook_url
        return {
            "configured": True,
            "webhook_preview": masked_url
        }
    return {
        "configured": False,
        "message": "Price alert webhook not configured"
    }

@app.post("/config/price-alert-webhook", tags=["Config"])
async def set_price_alert_webhook(request: PriceAlertWebhookRequest):
    """
    Set or update the price alert webhook URL.
    
    This webhook is separate from the regular alert webhooks and is used
    specifically for price alerts. Stored in discord_webhooks.json alongside other webhooks.
    """
    global PRICE_ALERT_WEBHOOK_URL
    
    try:
        webhook_url = request.webhook_url.strip()
        
        if not webhook_url:
            raise HTTPException(status_code=400, detail="webhook_url is required")
        
        # Save to webhook manager (discord_webhooks.json)
        webhook_manager.set_price_alert_webhook(webhook_url)
        
        # Also save to persistent config file for reliability across redeploys
        try:
            price_alert_config_file = "price_alert_webhook.txt"
            with open(price_alert_config_file, "w") as f:
                f.write(webhook_url)
            logger.info(f"Price alert webhook URL saved to config file: {price_alert_config_file}")
        except Exception as e:
            logger.warning(f"Failed to save price alert webhook to config file: {e}")
        
        PRICE_ALERT_WEBHOOK_URL = webhook_url
        logger.info(f"Price alert webhook URL updated: {webhook_url[:50]}...")
        
        return {
            "status": "success",
            "message": "Price alert webhook URL updated"
        }
        
    except Exception as e:
        logger.error(f"Failed to update price alert webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))

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
