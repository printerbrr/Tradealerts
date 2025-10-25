# Trade Alerts System v2.0

## Overview
Enhanced version of the Trade Alerts SMS Parser system with improved state management, better Discord formatting, and advanced monitoring capabilities.

## New Features
- **Enhanced Bearish/Bullish Detection**: Recognizes "bullish" and "bearish" keywords in alerts
- **Improved Discord Formatting**: Shows direction indicators in Discord messages
- **Smart State Updates**: Only updates database when status actually changes
- **Enhanced MACD Detection**: Better keyword recognition for MACD crossovers
- **Real-time State Monitoring**: Tools to monitor and sync state changes
- **Advanced Logging**: Detailed change detection and status tracking

## Files
- `main_v2.py` - Enhanced main application
- `sync_dev.py` - State synchronization tool
- `state_manager.py` - Enhanced state tracking system
- `confluence_rules.py` - Improved alert filtering rules
- `market_states.db` - Enhanced database schema
- `trade_alerts.log` - Enhanced production log file
- `discord_config.txt` - Discord webhook configuration

## Configuration
- **Port**: 8000
- **Database**: market_states.db
- **Log File**: trade_alerts.log
- **Discord Webhook**: Loaded from environment variable DISCORD_WEBHOOK_URL or discord_config.txt

## Enhanced Alert Types
- EMA Crossovers (9/21) with direction detection
- MACD Crossovers with improved keyword recognition
- High-confidence Schwab alerts
- Generic trade signals

## Enhanced Discord Message Format
```
**EMA CROSSOVER - 9/21 - BULLISH**
**TICKER:** SPY
**TIME FRAME:** 5MIN
**MARK:** $450.25
**TIME:** 06:14 PM
```

## New Capabilities
- **State Change Detection**: Logs when status changes vs. when it stays the same
- **Sync Tool**: `sync_dev.py` can sync unknown states with recent crossover data
- **Enhanced Logging**: Clear indicators for status changes and unchanged states
- **Better Error Handling**: Improved state management and error reporting

## State Management Improvements
- Checks current status before updating
- Only updates database when status actually changes
- Detailed logging of change detection
- Prevents unnecessary database writes

## Monitoring Tools
- `sync_dev.py` - Check and sync current states
- Real-time log monitoring capabilities
- State summary reporting
- Change detection logging

## Backward Compatibility
- Maintains all v1.0 functionality
- Same API endpoints
- Same configuration format
- Same database schema (enhanced)
