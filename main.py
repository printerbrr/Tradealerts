import os
import re
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from typing import Optional, Dict, Any
import logging
from datetime import datetime
import json

# Import state tracking modules
from state_manager import state_manager
from confluence_rules import confluence_rules

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
    
    # Load confluence rules
    confluence_rules.load_rules()
    logger.info(f"Confluence rules engine initialized")
    
except Exception as e:
    logger.error(f"Failed to initialize state tracking system: {e}")

@app.get("/")
async def root():
    return {
        "message": "Trade Alerts SMS Parser", 
        "status": "healthy",
        "version": "2.0.0",
        "mode": "production"
    }

@app.post("/webhook/sms")
async def receive_sms(request: Request):
    """
    Webhook endpoint to receive SMS messages forwarded from Tasker
    """
    try:
        # Get raw body to handle malformed JSON
        body = await request.body()
        logger.info(f"Raw request body: {body}")
        
        try:
            # Try to parse as JSON first
            data = await request.json()
            sender = data.get("sender", "unknown")
            message = data.get("message", "")
        except Exception as json_error:
            logger.warning(f"JSON parse failed: {json_error}")
            # Fallback: try to extract from raw body
            body_str = body.decode('utf-8', errors='ignore')
            logger.info(f"Raw body string: {body_str}")
            
            # Simple extraction for malformed JSON
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
                        # Handle both cases: message ends with quote followed by comma/brace, or multiline content
                        
                        # Try to find the end of the message field
                        remaining_text = body_str[start_pos:]
                        
                        # Look for patterns that indicate end of message field
                        end_patterns = [
                            r'",\s*"[^"]*"\s*:',  # message ends with ", followed by another field
                            r'",\s*}',            # message ends with ", followed by closing brace
                            r'"\s*}',             # message ends with " followed by closing brace
                            r'",\s*$',           # message ends with ", at end of string
                            r'"\s*$'             # message ends with " at end of string
                        ]
                        
                        message_end_pos = len(remaining_text)
                        for pattern in end_patterns:
                            end_match = re.search(pattern, remaining_text)
                            if end_match:
                                message_end_pos = end_match.start()
                                break
                        
                        # Extract the message content
                        message_content = remaining_text[:message_end_pos]
                        
                        # Clean up escaped characters and control characters
                        message = message_content.replace('\\n', '\n').replace('\\"', '"').replace('\\t', '\t').replace('\\r', '\r')
                        
                        # Remove any remaining control characters that might cause issues
                        message = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', message)
                        
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
        
        # Extract timeframe - handle all formats: 5MIN/5M, 15MIN/15M, 30MIN/30M, 1HR/1H/1HOUR, 2HR/2H/2HOUR, 4HR/4H/4HOUR, 1D/1DAY/1 DAY
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
    import pytz
    from datetime import datetime
    
    pacific = pytz.timezone('America/Los_Angeles')
    current_time_pacific = datetime.now(pacific)
    current_hour = current_time_pacific.hour
    
    # No alerts between 1 PM (13:00) and 4:59 AM (4:59)
    if 13 <= current_hour or current_hour < 5:
        logger.info(f"ALERT FILTERED: Current time {current_time_pacific.strftime('%I:%M %p')} is outside alert hours (5 AM - 1 PM PST/PDT)")
        return False
    
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
    Send alert to Discord webhook
    """
    if not alert_config.discord_webhook_url:
        logger.warning("Discord webhook URL not configured")
        return
    
    try:
        import requests
        
        parsed = log_data['parsed_data']
        
        # Simple, clean Discord message
        # Always use server receive time in PST/PDT (handles daylight savings automatically)
        import pytz
        from datetime import datetime
        
        # Get current time in Pacific timezone (handles PST/PDT automatically)
        pacific = pytz.timezone('America/Los_Angeles')
        server_time_pacific = datetime.now(pacific)
        display_time = server_time_pacific.strftime("%I:%M %p")
            
        # Create different message formats based on alert type
        if parsed.get('action') == 'macd_crossover':
            # MACD Crossover format
            macd_direction = parsed.get('macd_direction', 'bullish').upper()
            message = f"""**MACD CROSSOVER - {macd_direction}**
**TIME FRAME:** {parsed.get('timeframe', 'N/A')}
**MARK:** ${parsed.get('price', 'N/A')}
**TIME:** {display_time}"""
        else:
            # EMA Crossover format (existing)
            ema_pair = "N/A"
            if parsed.get('ema_short') and parsed.get('ema_long'):
                ema_pair = f"{parsed.get('ema_short')}/{parsed.get('ema_long')}"
            
            # Add direction to EMA crossover message
            ema_direction = parsed.get('ema_direction', 'bullish').upper()

            message = f"""**EMA CROSSOVER - {ema_pair} - {ema_direction}**
**TICKER:** {parsed.get('symbol', 'N/A')}
**TIME FRAME:** {parsed.get('timeframe', 'N/A')}
**MARK:** ${parsed.get('price', 'N/A')}
**TIME:** {display_time}"""
        
        payload = {
            "content": message
        }
        
        response = requests.post(
            alert_config.discord_webhook_url,
            json=payload,
            headers={"Content-Type": "application/json"}
        )
        
        if response.status_code == 204:
            logger.info("Discord alert sent successfully")
        else:
            logger.error(f"Failed to send Discord alert: {response.status_code}")
            
    except Exception as e:
        logger.error(f"Error sending Discord alert: {str(e)}")

@app.get("/config")
async def get_config():
    """Get current configuration"""
    return alert_config

@app.post("/config")
async def update_config(config: AlertConfig):
    """Update configuration"""
    global alert_config
    alert_config = config
    logger.info(f"Configuration updated: {config}")
    return {"status": "success", "message": "Configuration updated"}

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
