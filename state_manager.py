#!/usr/bin/env python3
"""
State Manager for Timeframe Tracking System
Manages persistent state tracking for EMA and MACD crossovers across timeframes
"""

import sqlite3
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
import os

logger = logging.getLogger(__name__)

# Timeframe hierarchy for confluence checking
TIMEFRAME_HIERARCHY = ["5MIN", "15MIN", "30MIN", "1HR", "2HR", "4HR", "1DAY"]

class StateManager:
    """Manages timeframe state persistence using SQLite database"""
    
    def __init__(self, database_path: str = "market_states.db"):
        self.database_path = database_path
        self.init_database()
    
    def init_database(self):
        """Initialize the SQLite database with required tables"""
        try:
            with sqlite3.connect(self.database_path) as conn:
                cursor = conn.cursor()
                
                # Create timeframe_states table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS timeframe_states (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        symbol TEXT NOT NULL,
                        timeframe TEXT NOT NULL,
                        ema_status TEXT DEFAULT 'UNKNOWN',
                        macd_status TEXT DEFAULT 'UNKNOWN',
                        last_ema_update TIMESTAMP,
                        last_macd_update TIMESTAMP,
                        last_ema_price REAL,
                        last_macd_price REAL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(symbol, timeframe)
                    )
                ''')
                
                # Create state_history table for tracking changes
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS state_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        symbol TEXT NOT NULL,
                        timeframe TEXT NOT NULL,
                        crossover_type TEXT NOT NULL,
                        old_status TEXT,
                        new_status TEXT NOT NULL,
                        price REAL,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                conn.commit()
                logger.info(f"[DEV] Database initialized: {self.database_path}")
                
        except Exception as e:
            logger.error(f"[DEV] Failed to initialize database: {e}")
            raise
    
    def update_timeframe_state(self, symbol: str, timeframe: str, crossover_type: str, direction: str, price: Optional[float] = None):
        """
        Update the state for a specific symbol/timeframe/crossover type
        
        Args:
            symbol: Stock symbol (e.g., "SPY")
            timeframe: Timeframe (e.g., "5MIN", "1HR")
            crossover_type: "ema" or "macd"
            direction: "bullish" or "bearish"
            price: Optional price at time of crossover
        """
        try:
            # Normalize inputs
            symbol = symbol.upper()
            timeframe = timeframe.upper()
            crossover_type = crossover_type.lower()
            direction = direction.upper()
            
            # Validate inputs
            if crossover_type not in ['ema', 'macd']:
                logger.error(f"[DEV] Invalid crossover_type: {crossover_type}")
                return False
            
            if direction not in ['BULLISH', 'BEARISH']:
                logger.error(f"[DEV] Invalid direction: {direction}")
                return False
            
            if timeframe not in TIMEFRAME_HIERARCHY:
                logger.warning(f"[DEV] Unknown timeframe: {timeframe}")
            
            with sqlite3.connect(self.database_path) as conn:
                cursor = conn.cursor()
                
                # Get current state
                cursor.execute('''
                    SELECT ema_status, macd_status, last_ema_price, last_macd_price
                    FROM timeframe_states 
                    WHERE symbol = ? AND timeframe = ?
                ''', (symbol, timeframe))
                
                result = cursor.fetchone()
                
                if result:
                    # Update existing record
                    current_ema_status, current_macd_status, current_ema_price, current_macd_price = result
                    
                    # Determine what to update
                    if crossover_type == 'ema':
                        new_ema_status = direction
                        new_macd_status = current_macd_status
                        new_ema_price = price
                        new_macd_price = current_macd_price
                        old_status = current_ema_status
                    else:  # macd
                        new_ema_status = current_ema_status
                        new_macd_status = direction
                        new_ema_price = current_ema_price
                        new_macd_price = price
                        old_status = current_macd_status
                    
                    # Update the record
                    cursor.execute('''
                        UPDATE timeframe_states 
                        SET ema_status = ?, macd_status = ?, 
                            last_ema_update = ?, last_macd_update = ?,
                            last_ema_price = ?, last_macd_price = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE symbol = ? AND timeframe = ?
                    ''', (new_ema_status, new_macd_status,
                          datetime.now() if crossover_type == 'ema' else None,
                          datetime.now() if crossover_type == 'macd' else None,
                          new_ema_price, new_macd_price,
                          symbol, timeframe))
                    
                else:
                    # Create new record
                    if crossover_type == 'ema':
                        ema_status, macd_status = direction, 'UNKNOWN'
                        ema_price, macd_price = price, None
                        old_status = 'UNKNOWN'
                    else:  # macd
                        ema_status, macd_status = 'UNKNOWN', direction
                        ema_price, macd_price = None, price
                        old_status = 'UNKNOWN'
                    
                    cursor.execute('''
                        INSERT INTO timeframe_states 
                        (symbol, timeframe, ema_status, macd_status, 
                         last_ema_update, last_macd_update, last_ema_price, last_macd_price)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (symbol, timeframe, ema_status, macd_status,
                          datetime.now() if crossover_type == 'ema' else None,
                          datetime.now() if crossover_type == 'macd' else None,
                          ema_price, macd_price))
                
                # Log the state change
                self.log_state_change(symbol, timeframe, crossover_type, old_status, direction, price)
                
                conn.commit()
                
                logger.info(f"[DEV] STATE UPDATE: {symbol} {timeframe} {crossover_type.upper()}: {old_status} -> {direction} (price: ${price})")
                return True
                
        except Exception as e:
            logger.error(f"[DEV] Failed to update timeframe state: {e}")
            return False
    
    def get_timeframe_state(self, symbol: str, timeframe: str) -> Optional[Dict[str, Any]]:
        """Get the current state for a specific symbol/timeframe"""
        try:
            symbol = symbol.upper()
            timeframe = timeframe.upper()
            
            with sqlite3.connect(self.database_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT ema_status, macd_status, last_ema_update, last_macd_update,
                           last_ema_price, last_macd_price, created_at, updated_at
                    FROM timeframe_states 
                    WHERE symbol = ? AND timeframe = ?
                ''', (symbol, timeframe))
                
                result = cursor.fetchone()
                
                if result:
                    return {
                        'symbol': symbol,
                        'timeframe': timeframe,
                        'ema_status': result[0],
                        'macd_status': result[1],
                        'last_ema_update': result[2],
                        'last_macd_update': result[3],
                        'last_ema_price': result[4],
                        'last_macd_price': result[5],
                        'created_at': result[6],
                        'updated_at': result[7]
                    }
                else:
                    return None
                    
        except Exception as e:
            logger.error(f"[DEV] Failed to get timeframe state: {e}")
            return None
    
    def get_all_states(self, symbol: str) -> Dict[str, Dict[str, Any]]:
        """Get all timeframe states for a specific symbol"""
        try:
            symbol = symbol.upper()
            
            with sqlite3.connect(self.database_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT timeframe, ema_status, macd_status, last_ema_update, last_macd_update,
                           last_ema_price, last_macd_price, created_at, updated_at
                    FROM timeframe_states 
                    WHERE symbol = ?
                    ORDER BY 
                        CASE timeframe
                            WHEN '5MIN' THEN 1
                            WHEN '15MIN' THEN 2
                            WHEN '30MIN' THEN 3
                            WHEN '1HR' THEN 4
                            WHEN '2HR' THEN 5
                            WHEN '4HR' THEN 6
                            WHEN '1DAY' THEN 7
                            ELSE 8
                        END
                ''', (symbol,))
                
                results = cursor.fetchall()
                
                states = {}
                for result in results:
                    timeframe = result[0]
                    states[timeframe] = {
                        'symbol': symbol,
                        'timeframe': timeframe,
                        'ema_status': result[1],
                        'macd_status': result[2],
                        'last_ema_update': result[3],
                        'last_macd_update': result[4],
                        'last_ema_price': result[5],
                        'last_macd_price': result[6],
                        'created_at': result[7],
                        'updated_at': result[8]
                    }
                
                return states
                
        except Exception as e:
            logger.error(f"[DEV] Failed to get all states: {e}")
            return {}
    
    def log_state_change(self, symbol: str, timeframe: str, crossover_type: str, 
                        old_status: str, new_status: str, price: Optional[float] = None):
        """Log state changes to history table"""
        try:
            with sqlite3.connect(self.database_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO state_history 
                    (symbol, timeframe, crossover_type, old_status, new_status, price)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (symbol.upper(), timeframe.upper(), crossover_type.lower(), 
                      old_status, new_status, price))
                conn.commit()
                
        except Exception as e:
            logger.error(f"Failed to log state change: {e}")
    
    def get_next_higher_timeframe(self, current_timeframe: str) -> Optional[str]:
        """Get the next higher timeframe in the hierarchy"""
        try:
            current_timeframe = current_timeframe.upper()
            
            if current_timeframe not in TIMEFRAME_HIERARCHY:
                return None
            
            current_index = TIMEFRAME_HIERARCHY.index(current_timeframe)
            
            if current_index < len(TIMEFRAME_HIERARCHY) - 1:
                return TIMEFRAME_HIERARCHY[current_index + 1]
            else:
                return None  # Already at highest timeframe
                
        except Exception as e:
            logger.error(f"[DEV] Failed to get next higher timeframe: {e}")
            return None
    
    def get_state_summary(self, symbol: str) -> Dict[str, Any]:
        """Get a summary of all states for a symbol"""
        states = self.get_all_states(symbol)
        
        summary = {
            'symbol': symbol,
            'total_timeframes': len(states),
            'ema_bullish_count': 0,
            'ema_bearish_count': 0,
            'macd_bullish_count': 0,
            'macd_bearish_count': 0,
            'timeframes': {}
        }
        
        for timeframe, state in states.items():
            summary['timeframes'][timeframe] = {
                'ema_status': state['ema_status'],
                'macd_status': state['macd_status']
            }
            
            if state['ema_status'] == 'BULLISH':
                summary['ema_bullish_count'] += 1
            elif state['ema_status'] == 'BEARISH':
                summary['ema_bearish_count'] += 1
                
            if state['macd_status'] == 'BULLISH':
                summary['macd_bullish_count'] += 1
            elif state['macd_status'] == 'BEARISH':
                summary['macd_bearish_count'] += 1
        
        return summary

# Global state manager instance
state_manager = StateManager()
