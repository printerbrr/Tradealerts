import os
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from typing import Optional, Dict, Any
import logging
from datetime import datetime
import json

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('trade_alerts.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Trade Alerts SMS Parser",
    description="A lean SMS-based trade alerting system",
    version="1.0.0"
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

# Load Discord webhook URL from environment variable
discord_webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
if discord_webhook_url:
    alert_config.discord_webhook_url = discord_webhook_url
    logger.info(f"Discord webhook URL loaded from environment: {discord_webhook_url[:50]}...")
else:
    logger.warning("DISCORD_WEBHOOK_URL environment variable not set")

@app.get("/")
async def root():
    return {"message": "Trade Alerts SMS Parser is running", "status": "healthy"}

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
                    # Extract message content, handling multiline and escaped quotes
                    msg_patterns = [
                        r'"message"\s*:\s*"([^"]*(?:\\.[^"]*)*)"',
                        r'"message"\s*:\s*"([^"]*)"'
                    ]
                    for pattern in msg_patterns:
                        match = re.search(pattern, body_str, re.DOTALL)
                        if match:
                            message = match.group(1)
                            # Clean up escaped characters
                            message = message.replace('\\n', '\n').replace('\\"', '"').replace('\\t', '\t')
                            break
                except:
                    pass
        
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
        
        # Extract timeframe
        tf_match = re.search(r'(\d+MIN\s*TF|\d+MINUTE\s*TF|MIN\s*TF)', message, re.IGNORECASE)
        if tf_match:
            parsed["timeframe"] = tf_match.group(1).strip()
        
        # Extract trigger time
        time_match = re.search(r'SUBMIT AT (\d+/\d+/\d+ \d+:\d+:\d+)', message, re.IGNORECASE)
        if time_match:
            parsed["trigger_time"] = time_match.group(1)
        
        # Extract study details
        study_match = re.search(r'STUDY\s*=\s*([\d.]+)', message, re.IGNORECASE)
        if study_match:
            parsed["study_details"] = study_match.group(1)
        
        # Detect crossover signals - improved detection
        crossover_keywords = [
            "movingavgcrossover", "crossover", "ema cross", "moving average",
            "length1", "length2", "exponential"
        ]
        
        if any(keyword in message_lower for keyword in crossover_keywords):
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

def analyze_data(parsed_data: Dict[str, Any]) -> bool:
    """
    Analyze parsed data against configured parameters
    Returns True if alert should be triggered
    Focus: Crossover detection for tomorrow's trading
    """
    # Primary focus: Moving Average Crossover detection
    if parsed_data.get("action") == "moving_average_crossover":
        logger.info("CROSSOVER DETECTED! Triggering Discord alert")
        return True
    
    # Secondary: High-confidence Schwab alerts
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
        current_time = datetime.now().strftime("%H:%M")
        message = f"""**EMA CROSSOVER DETECTED**
**TICKER:** {parsed.get('symbol', 'N/A')}
**TIME FRAME:** {parsed.get('timeframe', 'N/A')}
**MARK:** ${parsed.get('price', 'N/A')}
**TIME:** {current_time}"""
        
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
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
