from __future__ import annotations

from typing import Any, Dict, Optional

import os

import schwab
from schwab.auth import easy_client


class SchwabClient:
    """
    Thin wrapper around the Schwab Trader API using `schwab-py`.

    This implementation is currently focused on **read-only** access
    (quotes and account info). Order placement will be wired in later.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize the client from explicit config or environment variables.

        Expected config keys (all strings):
          - app_key: Schwab app key (client id)
          - app_secret: Schwab secret
          - redirect_uri: OAuth redirect URI (e.g. https://127.0.0.1:8182)
          - token_path: path to JSON file to store tokens (e.g. schwab_token.json)
        """

        cfg = dict(config or {})
        self._app_key = cfg.get("app_key") or os.getenv("SCHWAB_APP_KEY", "")
        self._app_secret = cfg.get("app_secret") or os.getenv("SCHWAB_APP_SECRET", "")
        self._redirect_uri = cfg.get("redirect_uri") or os.getenv(
            "SCHWAB_REDIRECT_URI", ""
        )
        self._token_path = cfg.get("token_path") or os.getenv(
            "SCHWAB_TOKEN_PATH", "schwab_token.json"
        )

        if not (self._app_key and self._app_secret and self._redirect_uri):
            raise ValueError(
                "Missing Schwab configuration. Ensure SCHWAB_APP_KEY, "
                "SCHWAB_APP_SECRET, and SCHWAB_REDIRECT_URI are set."
            )

        # easy_client will handle the initial OAuth login (opens a browser)
        # and subsequent token refreshes, persisting tokens to token_path.
        self._client = easy_client(
            api_key=self._app_key,
            app_secret=self._app_secret,
            callback_url=self._redirect_uri,
            token_path=self._token_path,
        )

    def get_quote(self, symbol: str) -> Dict[str, Any]:
        """
        Fetch a fresh quote for a symbol from Schwab.

        Returns the raw quote dictionary from schwab-py.
        """

        # Use Schwab's current quotes endpoint via schwab-py HTTP client.
        resp = self._client.get_quotes(symbols=[symbol])
        resp.raise_for_status()
        data = resp.json()
        # Response is typically a dict keyed by symbol.
        return data.get(symbol) or data

    def get_option_chain_0dte(self, underlying: str) -> Dict[str, Any]:
        """
        Fetch the 0DTE option chain for an underlying symbol.

        Returns the raw option chain JSON; the caller is responsible for
        selecting the desired contract (e.g., delta closest to +/-0.20).
        """

        # schwab-py exposes the HTTP client; use the documented
        # option chain endpoint wrapper if available.
        # We request expirations for today only (0DTE).
        resp = self._client.get_option_chain(
            symbol=underlying,
            contract_type="ALL",
            include_quotes=True,
        )
        resp.raise_for_status()
        return resp.json()

    def place_order(self, proposed_order: Any, policy: Dict[str, Any]) -> Dict[str, Any]:
        """
        Placeholder for future live order support.

        For the current paper-trading phase, this method should not be
        called. When we move to live trading, we will translate the
        ProposedOrder into a Schwab order payload and submit it here.
        """

        raise NotImplementedError(
            "place_order is not implemented in paper-trading mode."
        )

