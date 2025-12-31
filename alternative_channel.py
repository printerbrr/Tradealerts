#!/usr/bin/env python3
"""
Alternative Channel Signal System
Provides a separate Discord channel with different signal rules
Uses the same EMA state tracking but applies different filtering/formatting
"""

import logging
import httpx
import pytz
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from state_manager import state_manager

logger = logging.getLogger(__name__)


# Alternative channel webhook URL (loaded from config)
ALTERNATIVE_CHANNEL_WEBHOOK_URL = None

def load_alternative_webhook():
    """Load alternative channel webhook URL from environment or config file"""
    global ALTERNATIVE_CHANNEL_WEBHOOK_URL
    
    # Try environment variable first (Railway uses 1MIN_SIGNAL_WEBHOOK_URL)
    ALTERNATIVE_CHANNEL_WEBHOOK_URL = os.environ.get("1MIN_SIGNAL_WEBHOOK_URL")
    
    # Fallback to old env var name for backward compatibility
    if not ALTERNATIVE_CHANNEL_WEBHOOK_URL:
        ALTERNATIVE_CHANNEL_WEBHOOK_URL = os.environ.get("ALTERNATIVE_CHANNEL_WEBHOOK_URL")
    
    # Try config file if env var not set
    if not ALTERNATIVE_CHANNEL_WEBHOOK_URL:
        try:
            with open("alternative_channel_webhook.txt", "r") as f:
                ALTERNATIVE_CHANNEL_WEBHOOK_URL = f.read().strip()
                if ALTERNATIVE_CHANNEL_WEBHOOK_URL:
                    logger.info(f"Alternative channel webhook loaded from config file")
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning(f"Failed to load alternative channel webhook: {e}")
    
    if ALTERNATIVE_CHANNEL_WEBHOOK_URL:
        logger.info(f"Alternative channel webhook configured: {ALTERNATIVE_CHANNEL_WEBHOOK_URL[:50]}...")
    else:
        logger.info("Alternative channel webhook not configured - alternative channel disabled")

# Load on import
import os
load_alternative_webhook()

def analyze_alternative_channel(parsed_data: Dict[str, Any]) -> bool:
    """
    Analyze parsed data for alternative channel with specific rules:
    
    Rules:
    1. C1/P1 (1MIN EMA): Only if 5MIN EMA is in confluence
       - C1: 1MIN bullish + 5MIN must be BULLISH
       - P1: 1MIN bearish + 5MIN must be BEARISH
    2. C5/P5 (5MIN EMA): Always send, regardless of other timeframes
    
    Time Filter: 6 AM - 1 PM PST/PDT only
    Weekend Filter: No alerts on Saturday/Sunday
    
    Returns True if alert should be sent to alternative channel
    """
    try:
        # Time filter: 6 AM - 1 PM PST/PDT only
        pacific = pytz.timezone('America/Los_Angeles')
        current_time_pacific = datetime.now(pacific)
        
        # Check for weekend (Saturday=5, Sunday=6) - market is closed
        weekday = current_time_pacific.weekday()
        if weekday >= 5:  # Saturday (5) or Sunday (6)
            logger.info(f"ALTERNATIVE CHANNEL FILTERED: Current day is weekend ({current_time_pacific.strftime('%A')}) - market is closed")
            return False
        
        # Time filter: No alerts outside 6 AM - 1 PM PST/PDT
        current_hour = current_time_pacific.hour
        # No alerts between 1 PM (13:00) and 5:59 AM (5:59)
        if 13 <= current_hour or current_hour < 6:
            logger.info(f"ALTERNATIVE CHANNEL FILTERED: Current time {current_time_pacific.strftime('%I:%M %p')} is outside alert hours (6 AM - 1 PM PST/PDT)")
            return False
        
        action = parsed_data.get('action')
        
        # Only allow EMA crossovers (no MACD, no Squeeze)
        if action != 'moving_average_crossover':
            return False
        
        symbol = parsed_data.get('symbol', 'SPY')
        timeframe = parsed_data.get('timeframe', '').upper()
        ema_direction = parsed_data.get('ema_direction', '').upper()
        
        if not timeframe:
            return False
        
        if ema_direction not in ['BULLISH', 'BEARISH']:
            return False
        
        # Rule 1: 1MIN EMA crossovers - require 5MIN confluence
        if timeframe == '1MIN':
            # Get 5MIN EMA status
            states = state_manager.get_all_states(symbol)
            five_min_state = states.get('5MIN')
            
            if not five_min_state:
                logger.info("ALTERNATIVE CHANNEL: 1MIN signal filtered - no 5MIN state available")
                return False
            
            five_min_ema_status = (five_min_state.get('ema_status') or 'UNKNOWN').upper()
            
            # Check confluence: 1MIN direction must match 5MIN EMA status
            if ema_direction == 'BULLISH' and five_min_ema_status == 'BULLISH':
                logger.info("ALTERNATIVE CHANNEL: C1 signal - 1MIN bullish + 5MIN bullish (confluence)")
                return True
            elif ema_direction == 'BEARISH' and five_min_ema_status == 'BEARISH':
                logger.info("ALTERNATIVE CHANNEL: P1 signal - 1MIN bearish + 5MIN bearish (confluence)")
                return True
            else:
                logger.info(f"ALTERNATIVE CHANNEL: 1MIN signal filtered - no 5MIN confluence (1MIN={ema_direction}, 5MIN={five_min_ema_status})")
                return False
        
        # Rule 2: 5MIN EMA crossovers - always send
        elif timeframe == '5MIN':
            logger.info(f"ALTERNATIVE CHANNEL: C5/P5 signal - 5MIN {ema_direction.lower()} (always allowed)")
            return True
        
        # All other timeframes: don't send
        else:
            logger.info(f"ALTERNATIVE CHANNEL: Signal filtered - timeframe {timeframe} not allowed (only 1MIN and 5MIN)")
            return False
        
    except Exception as e:
        logger.error(f"Error analyzing alternative channel signal: {e}")
        return False

def format_alternative_channel_message(parsed_data: Dict[str, Any], log_data: Dict[str, Any]) -> Optional[str]:
    """
    Format message for alternative channel using the same format as main channel
    
    Format matches main channel:
    {emoji_str}
    {timeframe} EMA Cross - {tag}
    MARK: ${price}
    TIME: {display_time}
    @everyone
    
    Tags: C1, P1, C5, P5
    """
    try:
        action = parsed_data.get('action')
        
        # Only format EMA crossovers
        if action != 'moving_average_crossover':
            return None
        
        symbol = parsed_data.get('symbol', 'SPY').upper()
        timeframe = parsed_data.get('timeframe', '').upper()
        ema_direction = parsed_data.get('ema_direction', 'bullish').lower()
        price = parsed_data.get('price', 'N/A')
        
        # Get current time in Pacific timezone
        pacific = pytz.timezone('America/Los_Angeles')
        server_time_pacific = datetime.now(pacific)
        dst_offset = server_time_pacific.dst()
        tz_abbrev = "PDT" if dst_offset and dst_offset != timedelta(0) else "PST"
        display_time = server_time_pacific.strftime("%I:%M %p") + f" {tz_abbrev}"
        
        # Determine emoji count based on timeframe (same as main channel)
        def get_emoji_count(tf: str) -> int:
            if not tf:
                return 1
            tf = tf.upper()
            if tf in ['1MIN', '5MIN']:
                return 1
            elif tf in ['15MIN', '30MIN']:
                return 2
            elif tf in ['1HR', '2HR']:
                return 3
            elif tf in ['4HR', '1DAY', '4H', '1D']:
                return 4
            return 1
        
        # Get emoji string
        is_bullish = ema_direction == 'bullish'
        emoji_char = '🟢' if is_bullish else '🔴'
        emoji_count = get_emoji_count(timeframe)
        emoji_str = emoji_char * emoji_count
        
        # Determine tag: C1, P1, C5, P5
        if timeframe == '1MIN':
            tag = 'C1' if is_bullish else 'P1'
        elif timeframe == '5MIN':
            tag = 'C5' if is_bullish else 'P5'
        else:
            # Should not reach here, but safety check
            return None
        
        # Format message (same as main channel)
        title_tf = timeframe or 'N/A'
        message = f"""{emoji_str}
{title_tf} EMA Cross - {tag}
MARK: ${price}
TIME: {display_time}
@everyone"""
        
        return message
        
    except Exception as e:
        logger.error(f"Error formatting alternative channel message: {e}")
        return None

async def send_to_alternative_channel(parsed_data: Dict[str, Any], log_data: Dict[str, Any]) -> bool:
    """
    Send alert to alternative channel if rules are met
    
    This function:
    1. Checks if alternative channel is configured
    2. Analyzes signal with alternative rules
    3. Formats message with alternative format
    4. Sends to alternative channel webhook
    
    Returns True if sent successfully, False otherwise
    """
    try:
        global ALTERNATIVE_CHANNEL_WEBHOOK_URL
        
        # Check if alternative channel is configured
        if not ALTERNATIVE_CHANNEL_WEBHOOK_URL:
            return False  # Silently skip if not configured
        
        # Analyze with alternative rules
        should_send = analyze_alternative_channel(parsed_data)
        if not should_send:
            logger.debug("ALTERNATIVE CHANNEL: Signal filtered by alternative rules")
            return False
        
        # Format message with alternative format
        message = format_alternative_channel_message(parsed_data, log_data)
        if not message:
            logger.debug("ALTERNATIVE CHANNEL: Message formatting returned None")
            return False
        
        # Send to Discord using async httpx with timeout
        payload = {
            "content": message
        }
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.post(
                    ALTERNATIVE_CHANNEL_WEBHOOK_URL,
                    json=payload,
                    headers={"Content-Type": "application/json"}
                )
                
                if response.status_code == 204:
                    logger.info(f"Alternative channel alert sent successfully")
                    return True
                else:
                    error_msg = f"Failed to send alternative channel alert: {response.status_code}"
                    try:
                        response_text = response.text
                        if response_text:
                            error_msg += f" - Response: {response_text[:200]}"
                    except:
                        pass
                    logger.error(error_msg)
                    return False
            except httpx.TimeoutException:
                logger.error(f"Alternative channel webhook timeout after 10 seconds")
                return False
            except httpx.RequestError as e:
                logger.error(f"Alternative channel webhook request error: {e}")
                return False
            except Exception as e:
                logger.error(f"Unexpected error sending to alternative channel: {e}")
                return False
            
    except Exception as e:
        logger.error(f"Error sending to alternative channel: {str(e)}")
        return False

def set_alternative_webhook(webhook_url: str):
    """Set or update alternative channel webhook URL"""
    global ALTERNATIVE_CHANNEL_WEBHOOK_URL
    
    ALTERNATIVE_CHANNEL_WEBHOOK_URL = webhook_url.strip()
    
    # Save to config file for persistence
    try:
        with open("alternative_channel_webhook.txt", "w") as f:
            f.write(ALTERNATIVE_CHANNEL_WEBHOOK_URL)
        logger.info(f"Alternative channel webhook saved to config file")
    except Exception as e:
        logger.warning(f"Failed to save alternative channel webhook to config file: {e}")
    
    logger.info(f"Alternative channel webhook updated: {ALTERNATIVE_CHANNEL_WEBHOOK_URL[:50]}...")

def get_alternative_webhook() -> Optional[str]:
    """Get current alternative channel webhook URL"""
    return ALTERNATIVE_CHANNEL_WEBHOOK_URL

