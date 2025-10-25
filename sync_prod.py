#!/usr/bin/env python3
"""
Sync Development Script
Checks for unknown states in the dev database and syncs them with the most recent crossovers from the dev log
"""

import os
import re
import json
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional, Any
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Import state manager
from state_manager import StateManager, TIMEFRAME_HIERARCHY

class DevSync:
    """Syncs unknown states with recent crossover data from dev log"""
    
    def __init__(self, log_file: str = "trade_alerts_dev.log", dev_db: str = "market_states_dev.db"):
        self.log_file = log_file
        self.dev_db = dev_db
        self.crossovers = {}  # Will store parsed crossover data
        self.state_manager = StateManager(dev_db)  # Create dev-specific state manager
        
    def parse_log_crossovers(self):
        """Parse the dev log file to extract all crossover events"""
        logger.info(f"Parsing crossover data from {self.log_file}")
        
        if not os.path.exists(self.log_file):
            logger.error(f"Log file {self.log_file} not found")
            return
        
        try:
            with open(self.log_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Find all parsed data sections
            parsed_data_pattern = r'"parsed_data":\s*\{[^}]+"action":\s*"(?:macd_crossover|moving_average_crossover)"[^}]+"timeframe":\s*"([^"]+)"[^}]+"ema_direction":\s*"([^"]+)"[^}]+"macd_direction":\s*"([^"]+)"[^}]+"symbol":\s*"([^"]+)"[^}]+"price":\s*([0-9.]+)[^}]+"timestamp":\s*"([^"]+)"'
            
            # More flexible pattern to catch different JSON structures
            json_blocks = re.findall(r'"parsed_data":\s*(\{[^}]+\})', content)
            
            for json_block in json_blocks:
                try:
                    # Clean up the JSON block
                    json_block = json_block.replace('\\"', '"')
                    parsed_data = json.loads(json_block)
                    
                    # Check if this is a crossover event
                    action = parsed_data.get('action', '')
                    if action in ['macd_crossover', 'moving_average_crossover']:
                        symbol = parsed_data.get('symbol', 'SPY')
                        timeframe = parsed_data.get('timeframe', '')
                        price = parsed_data.get('price', 0)
                        timestamp = parsed_data.get('timestamp', '')
                        
                        if timeframe and timeframe.upper() in TIMEFRAME_HIERARCHY:
                            timeframe = timeframe.upper()
                            
                            # Initialize symbol if not exists
                            if symbol not in self.crossovers:
                                self.crossovers[symbol] = {}
                            
                            # Initialize timeframe if not exists
                            if timeframe not in self.crossovers[symbol]:
                                self.crossovers[symbol][timeframe] = {
                                    'ema_direction': None,
                                    'macd_direction': None,
                                    'ema_price': None,
                                    'macd_price': None,
                                    'ema_timestamp': None,
                                    'macd_timestamp': None
                                }
                            
                            # Update based on crossover type
                            if action == 'moving_average_crossover':
                                ema_direction = parsed_data.get('ema_direction', 'bullish')
                                self.crossovers[symbol][timeframe]['ema_direction'] = ema_direction
                                self.crossovers[symbol][timeframe]['ema_price'] = price
                                self.crossovers[symbol][timeframe]['ema_timestamp'] = timestamp
                                
                            elif action == 'macd_crossover':
                                macd_direction = parsed_data.get('macd_direction', 'bullish')
                                self.crossovers[symbol][timeframe]['macd_direction'] = macd_direction
                                self.crossovers[symbol][timeframe]['macd_price'] = price
                                self.crossovers[symbol][timeframe]['macd_timestamp'] = timestamp
                
                except json.JSONDecodeError:
                    continue
                except Exception as e:
                    logger.debug(f"Error parsing JSON block: {e}")
                    continue
            
            logger.info(f"Found crossover data for {len(self.crossovers)} symbols")
            for symbol, timeframes in self.crossovers.items():
                logger.info(f"  {symbol}: {len(timeframes)} timeframes")
                
        except Exception as e:
            logger.error(f"Error parsing log file: {e}")
    
    def get_unknown_states(self, symbol: str = "SPY") -> Dict[str, Dict[str, str]]:
        """Get all timeframes with unknown states for a symbol"""
        logger.info(f"Checking unknown states for {symbol}")
        
        # Get all states for the symbol
        all_states = self.state_manager.get_all_states(symbol)
        unknown_states = {}
        
        for timeframe in TIMEFRAME_HIERARCHY:
            if timeframe in all_states:
                state = all_states[timeframe]
                ema_status = state.get('ema_status', 'UNKNOWN')
                macd_status = state.get('macd_status', 'UNKNOWN')
                
                if ema_status == 'UNKNOWN' or macd_status == 'UNKNOWN':
                    unknown_states[timeframe] = {
                        'ema_status': ema_status,
                        'macd_status': macd_status
                    }
            else:
                # Timeframe doesn't exist in database, consider it unknown
                unknown_states[timeframe] = {
                    'ema_status': 'UNKNOWN',
                    'macd_status': 'UNKNOWN'
                }
        
        logger.info(f"Found {len(unknown_states)} timeframes with unknown states")
        return unknown_states
    
    def sync_unknown_states(self, symbol: str = "SPY"):
        """Sync unknown states with recent crossover data"""
        logger.info(f"Starting sync for {symbol}")
        
        # Parse crossover data from log
        self.parse_log_crossovers()
        
        # Get unknown states
        unknown_states = self.get_unknown_states(symbol)
        
        if not unknown_states:
            logger.info("No unknown states found - sync complete")
            return
        
        # Check if we have crossover data for this symbol
        if symbol not in self.crossovers:
            logger.warning(f"No crossover data found for {symbol}")
            return
        
        symbol_crossovers = self.crossovers[symbol]
        
        # Sync each unknown timeframe
        for timeframe, states in unknown_states.items():
            logger.info(f"Syncing {timeframe}...")
            
            if timeframe in symbol_crossovers:
                crossover_data = symbol_crossovers[timeframe]
                
                # Sync EMA status
                if states['ema_status'] == 'UNKNOWN' and crossover_data['ema_direction']:
                    ema_direction = crossover_data['ema_direction'].upper()
                    ema_price = crossover_data['ema_price']
                    
                    logger.info(f"  Updating EMA: UNKNOWN -> {ema_direction}")
                    success = self.state_manager.update_timeframe_state(
                        symbol, timeframe, 'ema', ema_direction.lower(), ema_price
                    )
                    if success:
                        logger.info(f"  ✅ EMA sync successful")
                    else:
                        logger.error(f"  ❌ EMA sync failed")
                
                # Sync MACD status
                if states['macd_status'] == 'UNKNOWN' and crossover_data['macd_direction']:
                    macd_direction = crossover_data['macd_direction'].upper()
                    macd_price = crossover_data['macd_price']
                    
                    logger.info(f"  Updating MACD: UNKNOWN -> {macd_direction}")
                    success = self.state_manager.update_timeframe_state(
                        symbol, timeframe, 'macd', macd_direction.lower(), macd_price
                    )
                    if success:
                        logger.info(f"  ✅ MACD sync successful")
                    else:
                        logger.error(f"  ❌ MACD sync failed")
            else:
                logger.warning(f"  No crossover data found for {timeframe}")
        
        logger.info("Sync complete!")
    
    def print_state_summary(self, symbol: str = "SPY"):
        """Print a summary of current states"""
        logger.info(f"\n=== STATE SUMMARY FOR {symbol} ===")
        
        # Get state summary
        summary = self.state_manager.get_state_summary(symbol)
        
        logger.info(f"Total timeframes: {summary['total_timeframes']}")
        logger.info(f"EMA Bullish: {summary['ema_bullish_count']}, Bearish: {summary['ema_bearish_count']}")
        logger.info(f"MACD Bullish: {summary['macd_bullish_count']}, Bearish: {summary['macd_bearish_count']}")
        
        logger.info("\nTimeframe Details:")
        for timeframe, state in summary['timeframes'].items():
            ema_status = state['ema_status']
            macd_status = state['macd_status']
            logger.info(f"  {timeframe:>6}: EMA={ema_status:>8}, MACD={macd_status:>8}")

def main():
    """Main function"""
    logger.info("=== DEV STATE SYNC SCRIPT ===")
    
    # Initialize sync
    sync = DevSync()
    
    # Print current state summary
    sync.print_state_summary("SPY")
    
    # Sync unknown states
    sync.sync_unknown_states("SPY")
    
    # Print updated state summary
    sync.print_state_summary("SPY")
    
    logger.info("=== SYNC COMPLETE ===")

if __name__ == "__main__":
    main()
