#!/usr/bin/env python3
"""
Alert Toggle Manager for Per-Symbol Alert Tag Toggles
Manages persistent alert toggle settings using SQLite database
"""
import sqlite3
import json
import os
import threading
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class AlertToggleManager:
    def __init__(self, database_path: str = "market_states.db"):
        self.database_path = database_path
        self._lock = threading.Lock()
        self._migrate_from_json()
    
    def _migrate_from_json(self):
        """Migrate existing JSON data to database if JSON file exists"""
        json_path = "alert_toggles.json"
        if not os.path.exists(json_path):
            return
        
        try:
            with open(json_path, "r") as f:
                json_data = json.load(f)
            
            if not isinstance(json_data, dict):
                logger.warning("alert_toggles.json has invalid format, skipping migration")
                return
            
            # Check if database already has any toggles
            try:
                with sqlite3.connect(self.database_path, timeout=30) as conn:
                    cursor = conn.cursor()
                    # Check if table exists first
                    cursor.execute('''
                        SELECT name FROM sqlite_master 
                        WHERE type='table' AND name='alert_toggles'
                    ''')
                    if cursor.fetchone() is None:
                        logger.info("alert_toggles table doesn't exist yet, skipping JSON migration")
                        return
                    
                    cursor.execute("SELECT COUNT(*) FROM alert_toggles")
                    count = cursor.fetchone()[0]
                    
                    if count > 0:
                        logger.info("Database already has alert toggles, skipping JSON migration")
                        return
            except sqlite3.OperationalError as e:
                logger.warning(f"Database not ready for migration check: {e}")
                return
            
            # Migrate JSON data to database
            migrated_count = 0
            with sqlite3.connect(self.database_path, timeout=30) as conn:
                cursor = conn.cursor()
                for symbol, toggles in json_data.items():
                    if not isinstance(toggles, dict):
                        continue
                    symbol = symbol.upper()
                    for tag, enabled in toggles.items():
                        if isinstance(enabled, bool):
                            try:
                                cursor.execute('''
                                    INSERT OR REPLACE INTO alert_toggles (symbol, tag, enabled, updated_at)
                                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                                ''', (symbol, tag, 1 if enabled else 0))
                                migrated_count += 1
                            except Exception as e:
                                logger.warning(f"Failed to migrate toggle {symbol}/{tag}: {e}")
                
                conn.commit()
                logger.info(f"Migrated {migrated_count} alert toggles from JSON to database")
        
        except Exception as e:
            logger.error(f"Failed to migrate alert toggles from JSON: {e}")

    def ensure_defaults(self, symbol: str):
        """Ensure default tags are enabled for a symbol"""
        # Default tags enabled: C, CALL, Call, P, PUT, Put, SQZ x common timeframes
        defaults: Dict[str, bool] = {}
        bases = ["C", "CALL", "Call", "P", "PUT", "Put", "SQZ"]
        tfs = ["1", "5", "15", "30", "1H", "2H", "4H", "1D"]
        for base in bases:
            for tf in tfs:
                defaults[f"{base}{tf}"] = True
        
        sym = symbol.upper()
        with self._lock:
            try:
                with sqlite3.connect(self.database_path, timeout=30) as conn:
                    cursor = conn.cursor()
                    
                    # Check existing toggles for this symbol
                    cursor.execute('''
                        SELECT tag FROM alert_toggles WHERE symbol = ?
                    ''', (sym,))
                    existing_tags = {row[0] for row in cursor.fetchall()}
                    
                    # Insert missing defaults
                    updated = False
                    for tag, enabled in defaults.items():
                        if tag not in existing_tags:
                            cursor.execute('''
                                INSERT INTO alert_toggles (symbol, tag, enabled, updated_at)
                                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                            ''', (sym, tag, 1 if enabled else 0))
                            updated = True
                    
                    conn.commit()
                    if updated:
                        logger.debug(f"Added default toggles for {sym}")
            except Exception as e:
                logger.error(f"Failed to ensure defaults for {sym}: {e}")

    def get(self, symbol: str) -> Dict[str, bool]:
        """Get all toggles for a symbol"""
        sym = symbol.upper()
        with self._lock:
            try:
                with sqlite3.connect(self.database_path, timeout=30) as conn:
                    cursor = conn.cursor()
                    cursor.execute('''
                        SELECT tag, enabled FROM alert_toggles WHERE symbol = ?
                    ''', (sym,))
                    results = cursor.fetchall()
                    return {tag: bool(enabled) for tag, enabled in results}
            except Exception as e:
                logger.error(f"Failed to get toggles for {sym}: {e}")
                return {}

    def set_many(self, symbol: str, updates: Dict[str, bool]) -> Dict[str, bool]:
        """Set multiple toggles at once for a symbol"""
        sym = symbol.upper()
        with self._lock:
            try:
                with sqlite3.connect(self.database_path, timeout=30) as conn:
                    cursor = conn.cursor()
                    
                    for tag, enabled in (updates or {}).items():
                        if not isinstance(enabled, bool):
                            continue
                        
                        # Preserve case for "Call" and "Put" bases, uppercase others
                        if tag.startswith("Call") or tag.startswith("Put"):
                            # Keep mixed case for Call/Put
                            normalized_tag = tag
                        else:
                            # Uppercase for C/P, CALL/PUT
                            normalized_tag = tag.upper()
                        
                        cursor.execute('''
                            INSERT OR REPLACE INTO alert_toggles (symbol, tag, enabled, updated_at)
                            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                        ''', (sym, normalized_tag, 1 if enabled else 0))
                    
                    conn.commit()
                    
                    # Return all toggles for this symbol (query directly, don't call self.get() to avoid deadlock)
                    cursor.execute('''
                        SELECT tag, enabled FROM alert_toggles WHERE symbol = ?
                    ''', (sym,))
                    results = cursor.fetchall()
                    return {tag: bool(enabled) for tag, enabled in results}
            except Exception as e:
                logger.error(f"Failed to set toggles for {sym}: {e}")
                return {}

    def is_enabled(self, symbol: str, tag: str) -> bool:
        """Check if a specific tag is enabled for a symbol (defaults to True if not found)"""
        sym = symbol.upper()
        with self._lock:
            try:
                with sqlite3.connect(self.database_path, timeout=30) as conn:
                    cursor = conn.cursor()
                    
                    # Check exact case first (case-sensitive matching)
                    cursor.execute('''
                        SELECT enabled FROM alert_toggles WHERE symbol = ? AND tag = ?
                    ''', (sym, tag))
                    result = cursor.fetchone()
                    if result:
                        return bool(result[0])
                    
                    # Default to True if not found
                    return True
            except Exception as e:
                logger.error(f"Failed to check toggle for {sym}/{tag}: {e}")
                return True


# Global alert toggle manager instance
# Will be initialized with correct database path in main.py
# Using default path initially, will be updated in main.py
alert_toggle_manager = AlertToggleManager("market_states.db")
