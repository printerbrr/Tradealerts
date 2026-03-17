from __future__ import annotations

from typing import Any, Dict, Optional

import os

from dotenv import load_dotenv

import schwab
from schwab.auth import easy_client, client_from_token_file

# Load .env so SCHWAB_* vars are available when running outside the main app (e.g. one-liner test).
load_dotenv()


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

        # For Railway/headless: if SCHWAB_TOKEN_JSON is set, write it to token_path
        # and load the client strictly from that file without falling back to
        # an interactive browser flow.
        token_json = os.getenv("SCHWAB_TOKEN_JSON")
        if token_json:
            try:
                with open(self._token_path, "w") as f:
                    f.write(token_json.strip())
                # Log to stdout so we can verify on Railway that the env var was seen.
                print(
                    f"[TradeBot] SCHWAB_TOKEN_JSON detected, wrote token to {self._token_path}"
                )
            except Exception as e:
                raise RuntimeError(
                    f"Failed to write SCHWAB_TOKEN_JSON to {self._token_path}: {e}"
                ) from e

            # In headless environments, never attempt browser-assisted login.
            # If the token is bad, this will raise, which is safer and easier
            # to diagnose than hanging for user input.
            self._client = client_from_token_file(
                token_path=self._token_path,
                api_key=self._app_key,
                app_secret=self._app_secret,
            )
        else:
            # Local/dev: use easy_client which will open a browser for the first
            # login and then refresh tokens automatically.
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

