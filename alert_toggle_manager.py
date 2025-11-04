#!/usr/bin/env python3
import json
import os
import threading
from typing import Dict


class AlertToggleManager:
    def __init__(self, path: str = "alert_toggles.json"):
        self.path = path
        self._lock = threading.Lock()
        self._data: Dict[str, Dict[str, bool]] = {}
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r") as f:
                    raw = json.load(f)
                self._data = raw if isinstance(raw, dict) else {}
            except Exception:
                self._data = {}
        else:
            self._data = {}

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self._data, f, indent=2)
        os.replace(tmp, self.path)

    def ensure_defaults(self, symbol: str):
        # Default tags enabled: C, CALL, Call, P, PUT, Put x common timeframes
        defaults: Dict[str, bool] = {}
        bases = ["C", "CALL", "Call", "P", "PUT", "Put"]
        tfs = ["1", "5", "15", "30", "1H", "2H", "4H", "1D"]
        for base in bases:
            for tf in tfs:
                defaults[f"{base}{tf}"] = True
        with self._lock:
            sym = symbol.upper()
            if sym not in self._data:
                self._data[sym] = defaults
                self._save()
            else:
                # Merge in any missing defaults (e.g., Call/Put for existing symbols)
                updated = False
                for key, value in defaults.items():
                    if key not in self._data[sym]:
                        self._data[sym][key] = value
                        updated = True
                if updated:
                    self._save()

    def get(self, symbol: str) -> Dict[str, bool]:
        sym = symbol.upper()
        with self._lock:
            return dict(self._data.get(sym, {}))

    def set_many(self, symbol: str, updates: Dict[str, bool]) -> Dict[str, bool]:
        sym = symbol.upper()
        with self._lock:
            current = self._data.get(sym, {})
            for k, v in (updates or {}).items():
                if isinstance(v, bool):
                    # Preserve case for "Call" and "Put" bases, uppercase others
                    key = k
                    if key.startswith("Call") or key.startswith("Put"):
                        # Keep mixed case for Call/Put
                        current[key] = v
                    else:
                        # Uppercase for C/P, CALL/PUT
                        current[k.upper()] = v
            self._data[sym] = current
            self._save()
            return dict(current)

    def is_enabled(self, symbol: str, tag: str) -> bool:
        sym = symbol.upper()
        with self._lock:
            mapping = self._data.get(sym, {})
            if not mapping:
                return True
            # Check exact case first, then uppercase fallback
            if tag in mapping:
                return mapping[tag]
            key = tag.upper()
            if key in mapping:
                return mapping[key]
            # Also check if it's a Call/Put variant
            if tag.startswith("Call") or tag.startswith("Put"):
                key_mixed = tag
                if key_mixed in mapping:
                    return mapping[key_mixed]
            return mapping.get(key, True)


alert_toggle_manager = AlertToggleManager()


