#!/usr/bin/env python3
"""
Confluence Rules Engine for Alert Filtering
Evaluates configurable rules to determine if alerts should be sent based on timeframe confluence
"""

import json
import logging
import os
from typing import Dict, List, Any, Optional
from state_manager import state_manager, TIMEFRAME_HIERARCHY

logger = logging.getLogger(__name__)

class ConfluenceRulesEngine:
    """Manages and evaluates confluence rules for alert filtering"""
    
    def __init__(self, rules_file: str = "confluence_rules.json"):
        self.rules_file = rules_file
        self.rules = []
        self.load_rules()
    
    def load_rules(self):
        """Load rules from JSON configuration file"""
        try:
            if os.path.exists(self.rules_file):
                with open(self.rules_file, 'r') as f:
                    config = json.load(f)
                    self.rules = config.get('rules', [])
                logger.info(f"[DEV] Loaded {len(self.rules)} confluence rules from {self.rules_file}")
            else:
                # Create default rules if file doesn't exist
                self.create_default_rules()
                logger.info(f"[DEV] Created default confluence rules file: {self.rules_file}")
                
        except Exception as e:
            logger.error(f"[DEV] Failed to load confluence rules: {e}")
            self.rules = []
    
    def create_default_rules(self):
        """Create default confluence rules configuration"""
        default_rules = {
            "rules": [
                {
                    "name": "MACD confluence with next higher EMA",
                    "enabled": True,
                    "trigger": {
                        "timeframe": "any",
                        "crossover_type": "macd",
                        "direction": "any"
                    },
                    "requirements": [
                        {
                            "timeframe": "next_higher",
                            "check": "ema_status",
                            "must_be": "same_direction"
                        }
                    ],
                    "action": "ALLOW"
                },
                {
                    "name": "EMA confluence with next higher EMA",
                    "enabled": True,
                    "trigger": {
                        "timeframe": "any",
                        "crossover_type": "ema",
                        "direction": "any"
                    },
                    "requirements": [
                        {
                            "timeframe": "next_higher",
                            "check": "ema_status",
                            "must_be": "same_direction"
                        }
                    ],
                    "action": "ALLOW"
                },
                {
                    "name": "Block alerts without confluence",
                    "enabled": False,
                    "trigger": {
                        "timeframe": "any",
                        "crossover_type": "any",
                        "direction": "any"
                    },
                    "requirements": [],
                    "action": "BLOCK"
                }
            ]
        }
        
        try:
            with open(self.rules_file, 'w') as f:
                json.dump(default_rules, f, indent=2)
            self.rules = default_rules['rules']
        except Exception as e:
            logger.error(f"[DEV] Failed to create default rules file: {e}")
            self.rules = []
    
    def reload_rules(self):
        """Reload rules from file (useful for runtime updates)"""
        self.load_rules()
    
    def get_applicable_rules(self, parsed_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Find rules that apply to the current alert"""
        applicable_rules = []
        
        alert_timeframe = parsed_data.get('timeframe', '').upper()
        alert_action = parsed_data.get('action', '')
        alert_direction = parsed_data.get('macd_direction', parsed_data.get('ema_direction', '')).upper()
        
        # Determine crossover type
        crossover_type = 'unknown'
        if alert_action == 'macd_crossover':
            crossover_type = 'macd'
        elif alert_action == 'moving_average_crossover':
            crossover_type = 'ema'
        
        for rule in self.rules:
            if not rule.get('enabled', True):
                continue
            
            trigger = rule.get('trigger', {})
            
            # Check if rule applies to this alert
            if self._matches_trigger(trigger, alert_timeframe, crossover_type, alert_direction):
                applicable_rules.append(rule)
        
        return applicable_rules
    
    def _matches_trigger(self, trigger: Dict[str, str], timeframe: str, crossover_type: str, direction: str) -> bool:
        """Check if a trigger matches the current alert"""
        trigger_timeframe = trigger.get('timeframe', 'any')
        trigger_crossover_type = trigger.get('crossover_type', 'any')
        trigger_direction = trigger.get('direction', 'any')
        
        # Check timeframe match
        if trigger_timeframe != 'any' and trigger_timeframe != timeframe:
            return False
        
        # Check crossover type match
        if trigger_crossover_type != 'any' and trigger_crossover_type != crossover_type:
            return False
        
        # Check direction match
        if trigger_direction != 'any' and trigger_direction != direction:
            return False
        
        return True
    
    def evaluate_alert(self, parsed_data: Dict[str, Any], current_states: Dict[str, Dict[str, Any]]) -> bool:
        """
        Evaluate if an alert should be sent based on confluence rules
        
        Returns:
            True if alert should be sent, False if it should be blocked
        """
        try:
            symbol = parsed_data.get('symbol', 'SPY')
            applicable_rules = self.get_applicable_rules(parsed_data)
            
            if not applicable_rules:
                logger.info(f"[DEV] CONFLUENCE CHECK: No applicable rules for {symbol} - ALLOWING alert")
                return True
            
            logger.info(f"[DEV] CONFLUENCE CHECK: Evaluating {len(applicable_rules)} rules for {symbol}")
            
            for rule in applicable_rules:
                rule_name = rule.get('name', 'Unnamed Rule')
                requirements = rule.get('requirements', [])
                action = rule.get('action', 'ALLOW')
                
                # Check if all requirements are met
                if self._check_rule_requirements(rule, parsed_data, current_states):
                    logger.info(f"[DEV] CONFLUENCE CHECK: Rule '{rule_name}' PASSED - Action: {action}")
                    return action == 'ALLOW'
                else:
                    logger.info(f"[DEV] CONFLUENCE CHECK: Rule '{rule_name}' FAILED")
            
            # If no rules passed, default behavior depends on configuration
            logger.info(f"[DEV] CONFLUENCE CHECK: No rules passed - defaulting to BLOCK")
            return False
            
        except Exception as e:
            logger.error(f"[DEV] Failed to evaluate confluence rules: {e}")
            return True  # Default to allowing alerts if evaluation fails
    
    def _check_rule_requirements(self, rule: Dict[str, Any], parsed_data: Dict[str, Any], 
                                current_states: Dict[str, Dict[str, Any]]) -> bool:
        """Check if all requirements for a rule are met"""
        requirements = rule.get('requirements', [])
        symbol = parsed_data.get('symbol', 'SPY')
        
        for requirement in requirements:
            if not self._check_single_requirement(requirement, parsed_data, current_states, symbol):
                return False
        
        return True
    
    def _check_single_requirement(self, requirement: Dict[str, str], parsed_data: Dict[str, Any],
                                 current_states: Dict[str, Dict[str, Any]], symbol: str) -> bool:
        """Check a single requirement"""
        try:
            timeframe = requirement.get('timeframe', '')
            check_type = requirement.get('check', '')
            must_be = requirement.get('must_be', '')
            
            # Handle special timeframe keywords
            if timeframe == 'next_higher':
                current_timeframe = parsed_data.get('timeframe', '').upper()
                timeframe = state_manager.get_next_higher_timeframe(current_timeframe)
                if not timeframe:
                    logger.info(f"[DEV] CONFLUENCE CHECK: No higher timeframe for {current_timeframe}")
                    return False
            
            # Get the state for the required timeframe
            if timeframe in current_states:
                state = current_states[timeframe]
            else:
                logger.info(f"[DEV] CONFLUENCE CHECK: No state found for {symbol} {timeframe}")
                return False
            
            # Get the current status based on check_type
            if check_type == 'ema_status':
                current_status = state.get('ema_status', 'UNKNOWN')
            elif check_type == 'macd_status':
                current_status = state.get('macd_status', 'UNKNOWN')
            else:
                logger.warning(f"[DEV] CONFLUENCE CHECK: Unknown check_type: {check_type}")
                return False
            
            # Check if status matches requirement
            if must_be == 'same_direction':
                alert_direction = parsed_data.get('macd_direction', parsed_data.get('ema_direction', '')).upper()
                required_status = alert_direction
            else:
                required_status = must_be.upper()
            
            result = current_status == required_status
            
            logger.info(f"[DEV] CONFLUENCE CHECK: {symbol} {timeframe} {check_type} is {current_status}, required: {required_status} - {'PASS' if result else 'FAIL'}")
            
            return result
            
        except Exception as e:
            logger.error(f"[DEV] Failed to check single requirement: {e}")
            return False
    
    def get_rule_summary(self) -> Dict[str, Any]:
        """Get a summary of all loaded rules"""
        summary = {
            'total_rules': len(self.rules),
            'enabled_rules': len([r for r in self.rules if r.get('enabled', True)]),
            'disabled_rules': len([r for r in self.rules if not r.get('enabled', True)]),
            'rules': []
        }
        
        for rule in self.rules:
            summary['rules'].append({
                'name': rule.get('name', 'Unnamed'),
                'enabled': rule.get('enabled', True),
                'trigger': rule.get('trigger', {}),
                'requirements_count': len(rule.get('requirements', [])),
                'action': rule.get('action', 'ALLOW')
            })
        
        return summary

# Global confluence rules engine instance
confluence_rules = ConfluenceRulesEngine()
