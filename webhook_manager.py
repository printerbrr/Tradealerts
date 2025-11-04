#!/usr/bin/env python3
"""
Webhook Manager for Multi-Symbol Alert System
Manages Discord webhook URLs per symbol with fallback support
"""

import json
import logging
import os
from typing import Dict, Optional

logger = logging.getLogger(__name__)

class WebhookManager:
    """Manages Discord webhook URLs per symbol"""
    
    def __init__(self, config_file: str = "discord_webhooks.json"):
        self.config_file = config_file
        self.webhooks = {}
        self.load_webhooks()
    
    def load_webhooks(self):
        """Load webhook URLs from JSON config file"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
                    self.webhooks = config.get('webhooks', {})
                logger.info(f"Loaded webhook configuration from {self.config_file}")
                logger.info(f"Webhooks configured for: {list(self.webhooks.keys())}")
                # Migrate price alert webhook from old file if needed
                self.migrate_price_alert_webhook()
            else:
                # Create default config with SPY webhook from discord_config.txt
                self.create_default_config()
                logger.info(f"Created default webhook configuration: {self.config_file}")
                # Migrate price alert webhook from old file if needed
                self.migrate_price_alert_webhook()
        except Exception as e:
            logger.error(f"Failed to load webhook configuration: {e}")
            self.webhooks = {}
            # Try to load from old discord_config.txt
            self.load_legacy_config()
            # Migrate price alert webhook from old file if needed
            self.migrate_price_alert_webhook()
    
    def migrate_price_alert_webhook(self):
        """Migrate price alert webhook from old price_alert_webhook.txt file to JSON"""
        price_alert_file = "price_alert_webhook.txt"
        if os.path.exists(price_alert_file) and not self.get_price_alert_webhook():
            try:
                with open(price_alert_file, "r") as f:
                    webhook_url = f.read().strip()
                    if webhook_url:
                        self.webhooks["PRICE_ALERT"] = webhook_url
                        self.save_webhooks()
                        logger.info("Migrated price alert webhook from price_alert_webhook.txt to discord_webhooks.json")
            except Exception as e:
                logger.warning(f"Failed to migrate price alert webhook: {e}")
    
    def create_default_config(self):
        """Create default webhook configuration"""
        # Try to load existing webhook from discord_config.txt
        fallback_url = None
        try:
            with open("discord_config.txt", "r") as f:
                fallback_url = f.read().strip()
        except FileNotFoundError:
            pass
        
        default_config = {
            "webhooks": {
                "SPY": fallback_url or "",
                "default": fallback_url or ""
            },
            "notes": {
                "SPY": "Main trading symbol",
                "default": "Fallback webhook for unmapped symbols"
            }
        }
        
        try:
            with open(self.config_file, 'w') as f:
                json.dump(default_config, f, indent=2)
            self.webhooks = default_config['webhooks']
        except Exception as e:
            logger.error(f"Failed to create default webhook config: {e}")
    
    def load_legacy_config(self):
        """Load webhook from old discord_config.txt file for backward compatibility"""
        try:
            with open("discord_config.txt", "r") as f:
                legacy_url = f.read().strip()
                if legacy_url:
                    self.webhooks = {
                        "SPY": legacy_url,
                        "default": legacy_url
                    }
                    logger.info("Loaded legacy webhook from discord_config.txt")
        except FileNotFoundError:
            logger.warning("No webhook configuration found")
    
    def get_webhook(self, symbol: str) -> Optional[str]:
        """
        Get webhook URL for a specific symbol
        Returns symbol-specific webhook, or default, or None
        """
        symbol = symbol.upper()
        
        # Try symbol-specific webhook first
        if symbol in self.webhooks and self.webhooks[symbol]:
            return self.webhooks[symbol]
        
        # Fallback to default
        if "default" in self.webhooks and self.webhooks["default"]:
            return self.webhooks["default"]
        
        logger.warning(f"No webhook configured for {symbol} and no default found")
        return None
    
    def set_webhook(self, symbol: str, webhook_url: str):
        """Set or update webhook URL for a symbol"""
        symbol = symbol.upper()
        self.webhooks[symbol] = webhook_url
        self.save_webhooks()
        logger.info(f"Updated webhook for {symbol}")
    
    def remove_webhook(self, symbol: str) -> bool:
        """Remove webhook for a symbol (but keep default)"""
        symbol = symbol.upper()
        if symbol in self.webhooks and symbol != "default":
            del self.webhooks[symbol]
            self.save_webhooks()
            logger.info(f"Removed webhook for {symbol}")
            return True
        return False
    
    def save_webhooks(self):
        """Save webhook configuration to file"""
        try:
            config = {
                "webhooks": self.webhooks,
                "notes": {}
            }
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)
            logger.info(f"Saved webhook configuration to {self.config_file}")
        except Exception as e:
            logger.error(f"Failed to save webhook configuration: {e}")
    
    def get_all_symbols(self) -> list:
        """Get list of all configured symbols (excluding default and PRICE_ALERT)"""
        return [s for s in self.webhooks.keys() if s not in ["default", "PRICE_ALERT", "price_alert"]]
    
    def get_config(self) -> Dict[str, str]:
        """Get full webhook configuration"""
        return {
            'webhooks': self.webhooks,
            'total_symbols': len([k for k in self.webhooks.keys() if k != 'default']),
            'has_default': 'default' in self.webhooks
        }
    
    def update_webhook(self, symbol: str, webhook_url: str) -> bool:
        """Update existing webhook or add new one"""
        symbol = symbol.upper()
        old_url = self.webhooks.get(symbol)
        self.set_webhook(symbol, webhook_url)
        return old_url is not None  # Returns True if updating, False if adding new
    
    def get_price_alert_webhook(self) -> Optional[str]:
        """Get price alert webhook URL"""
        return self.webhooks.get("PRICE_ALERT") or self.webhooks.get("price_alert")
    
    def set_price_alert_webhook(self, webhook_url: str):
        """Set or update price alert webhook URL"""
        self.webhooks["PRICE_ALERT"] = webhook_url
        self.save_webhooks()
        logger.info("Updated price alert webhook")

# Global webhook manager instance
webhook_manager = WebhookManager()

