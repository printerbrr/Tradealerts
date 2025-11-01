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
        # Default tags enabled: C, CALL, P, PUT x common timeframes
        defaults: Dict[str, bool] = {}
        bases = ["C", "CALL", "P", "PUT"]
        tfs = ["1", "5", "15", "30", "1H", "2H", "4H", "1D"]
        for base in bases:
            for tf in tfs:
                defaults[f"{base}{tf}"] = True
        with self._lock:
            sym = symbol.upper()
            if sym not in self._data:
                self._data[sym] = defaults
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
                    current[k.upper()] = v
            self._data[sym] = current
            self._save()
            return dict(current)

    def is_enabled(self, symbol: str, tag: str) -> bool:
        sym = symbol.upper()
        key = tag.upper()
        with self._lock:
            mapping = self._data.get(sym, {})
            if not mapping:
                return True
            return mapping.get(key, True)


alert_toggle_manager = AlertToggleManager()


