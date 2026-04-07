"""
Headless monitor for FinancialJuice home feed: posts every news item to Discord.

Message = DD/MM/YY HH:MM (timezone configurable) + headline + optional body (no article URL).

Items with class active-critical (red breaking) prepend @everyone (requires the
webhook/channel to allow @everyone mentions).

Loads webhook from NEWS_ALERTS_DISCORD_WEBHOOK_URL, DISCORD_WEBHOOK_URL, or
news_alerts_webhook.txt.

First run: records all visible headline IDs without posting (avoids spamming history).

Usage (local):
  pip install -r requirements.txt
  python -m playwright install chromium
  set NEWS_ALERTS_DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
  python monitor_critical_news.py

Railway (recommended): build from Dockerfile.newsmonitor (Playwright image includes OS libs + browsers).
  Service → Settings → Build → Dockerfile path → Dockerfile.newsmonitor

Railpack fallback (often fails missing .so at runtime): PLAYWRIGHT_BROWSERS_PATH=0 pip install -r requirements.txt
  && sh scripts/install_playwright_railway.sh
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import pytz
import requests
from dotenv import load_dotenv

load_dotenv()
# Bundle browsers in site-packages (Playwright "0" path). Required on Railway: /root/.cache
# from `playwright install` is not present at runtime.
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")

from playwright.sync_api import sync_playwright

SCRIPT_DIR = Path(__file__).resolve().parent
STATE_PATH = SCRIPT_DIR / "seen_news_ids.json"
WEBHOOK_FILE = SCRIPT_DIR / "news_alerts_webhook.txt"

FJ_URL = os.environ.get("FINANCIALJUICE_URL", "https://www.financialjuice.com/home")
POLL_SECONDS = float(os.environ.get("NEWS_ALERTS_POLL_SECONDS", "10"))
NAV_TIMEOUT_MS = int(os.environ.get("NEWS_ALERTS_NAV_TIMEOUT_MS", "60000"))
# Seconds to wait after feed DOM attaches (let JS paint headlines)
FEED_SETTLE_SECONDS = float(os.environ.get("NEWS_ALERTS_FEED_SETTLE_SECONDS", "0.4"))
# Block images/fonts/media on the page load to reduce latency (feed text is in DOM)
BLOCK_HEAVY_RESOURCES = os.environ.get("NEWS_ALERTS_BLOCK_RESOURCES", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
# Extra wait + second parse so "breaking" CSS can apply after the row first appears as news-general
CRITICAL_RECHECK_SECONDS = float(os.environ.get("NEWS_ALERTS_CRITICAL_RECHECK_SECONDS", "0.8"))
FEED_ROW = ".feedWrap"
FEED_READY = ".feedWrap"
CRITICAL_SELECTOR = ".feedWrap.active-critical"
# IANA timezone for the leading timestamp (Railway is often UTC)
NEWS_ALERTS_TIMEZONE = os.environ.get("NEWS_ALERTS_TIMEZONE", "UTC").strip() or "UTC"
# Discord webhook rate limits: space posts + retry on 429 (Retry-After)
DISCORD_MIN_GAP_SECONDS = float(
    os.environ.get("NEWS_ALERTS_DISCORD_MIN_GAP_SECONDS", "1.25")
)
DISCORD_MAX_RETRIES_429 = int(os.environ.get("NEWS_ALERTS_DISCORD_MAX_RETRIES", "12"))

logging.basicConfig(
    level=os.environ.get("NEWS_ALERTS_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("financialjuice_news")

_last_discord_post_mono: float = 0.0


def _discord_spacing_wait() -> None:
    """Avoid bursting many webhooks in one poll (Discord ~30/min per webhook)."""
    global _last_discord_post_mono
    gap = DISCORD_MIN_GAP_SECONDS
    if gap <= 0:
        return
    now = time.monotonic()
    wait = gap - (now - _last_discord_post_mono)
    if wait > 0:
        time.sleep(wait)
    _last_discord_post_mono = time.monotonic()


def _post_discord_payload(webhook: str, payload: dict) -> None:
    """POST with 429 retries (Retry-After). 404 = invalid/deleted webhook, no retry."""
    attempt = 0
    while True:
        r = requests.post(webhook, json=payload, timeout=30)
        if r.status_code == 429:
            attempt += 1
            if attempt > DISCORD_MAX_RETRIES_429:
                logger.error(
                    "Discord 429 after %s retries; give up (check min gap / volume)",
                    DISCORD_MAX_RETRIES_429,
                )
                r.raise_for_status()
            try:
                wait_s = float(r.headers.get("Retry-After", "2"))
            except (TypeError, ValueError):
                wait_s = 2.0
            logger.warning(
                "Discord rate limited (429), sleeping %.1fs then retry %s/%s",
                wait_s,
                attempt,
                DISCORD_MAX_RETRIES_429,
            )
            time.sleep(wait_s)
            continue
        if r.status_code == 404:
            logger.error(
                "Discord webhook returned 404 — URL invalid or webhook was deleted. "
                "Update NEWS_ALERTS_DISCORD_WEBHOOK_URL in Railway."
            )
        r.raise_for_status()
        return


def resolve_webhook() -> str | None:
    for key in ("NEWS_ALERTS_DISCORD_WEBHOOK_URL", "DISCORD_WEBHOOK_URL"):
        v = os.environ.get(key)
        if v and v.strip():
            return v.strip()
    if WEBHOOK_FILE.is_file():
        text = WEBHOOK_FILE.read_text(encoding="utf-8").strip()
        if text:
            return text
    return None


def load_seen() -> set[str]:
    if not STATE_PATH.is_file():
        return set()
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {str(x) for x in data}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Could not read state file %s: %s", STATE_PATH, e)
    return set()


def save_seen(seen: set[str]) -> None:
    STATE_PATH.write_text(
        json.dumps(sorted(seen), indent=0),
        encoding="utf-8",
    )


_NEWS_ID_RE = re.compile(r"/News/(\d+)/", re.I)


def _row_article_body(el) -> str:
    """Extra paragraph/body under the headline (no link)."""
    try:
        raw = el.locator(".headline-content").first.inner_text(timeout=3_000)
        return raw.strip()
    except Exception:
        return ""


def parse_feed_rows(page) -> list[dict[str, str | bool]]:
    """Return list of {id, title, body, url, critical} for each .feedWrap row."""
    out: list[dict[str, str | bool]] = []
    for el in page.locator(FEED_ROW).all():
        try:
            cls = (el.get_attribute("class") or "").strip()
        except Exception:
            cls = ""
        critical = "active-critical" in cls
        try:
            title_el = el.locator(".headline-title-nolink").first
            title = title_el.inner_text(timeout=5_000).strip()
        except Exception:
            continue
        if not title:
            continue
        article_body = _row_article_body(el)
        url = ""
        try:
            nav = el.locator("ul.social-nav").first
            url = (nav.get_attribute("data-link") or "").strip()
        except Exception:
            pass
        m = _NEWS_ID_RE.search(url)
        nid = m.group(1) if m else None
        if not nid:
            nid = str(abs(hash(title)))
        out.append(
            {
                "id": str(nid),
                "title": title,
                "body": article_body,
                "url": url,
                "critical": critical,
            }
        )
    return out


def merge_feed_rows(
    rows_a: list[dict[str, str | bool]],
    rows_b: list[dict[str, str | bool]],
) -> list[dict[str, str | bool]]:
    """Merge two parses by id; critical is True if either pass had it."""
    by_id: dict[str, dict[str, str | bool]] = {}
    for r in rows_a + rows_b:
        rid = str(r["id"])
        if rid not in by_id:
            by_id[rid] = dict(r)
        else:
            by_id[rid]["critical"] = bool(by_id[rid]["critical"]) or bool(r["critical"])
            b1 = str(by_id[rid].get("body", "")).strip()
            b2 = str(r.get("body", "")).strip()
            if len(b2) > len(b1):
                by_id[rid]["body"] = b2
    return list(by_id.values())


def critical_news_ids_from_page(page) -> set[str]:
    """IDs that appear under .feedWrap.active-critical (authoritative for breaking/red)."""
    ids: set[str] = set()
    for el in page.locator(CRITICAL_SELECTOR).all():
        url = ""
        try:
            nav = el.locator("ul.social-nav").first
            url = (nav.get_attribute("data-link") or "").strip()
        except Exception:
            pass
        m = _NEWS_ID_RE.search(url)
        if m:
            ids.add(m.group(1))
            continue
        try:
            title = el.locator(".headline-title-nolink").first.inner_text(
                timeout=5_000
            ).strip()
        except Exception:
            continue
        if title:
            ids.add(str(abs(hash(title))))
    return ids


def _message_timestamp_line() -> str:
    """DD/MM/YY Hour:Minute (no seconds).

    Uses pytz (already in requirements) — stdlib zoneinfo needs OS/tzdata data
    missing in some Docker images (ModuleNotFoundError: tzdata).
    """
    try:
        tz = pytz.timezone(NEWS_ALERTS_TIMEZONE)
    except Exception:
        tz = pytz.UTC
    return datetime.now(tz).strftime("%d/%m/%y %H:%M")


def apply_critical_flags(
    rows: list[dict[str, str | bool]], crit_ids: set[str]
) -> None:
    """Set critical=True when id is in the breaking-news set (overrides class timing issues)."""
    for row in rows:
        rid = str(row["id"])
        row["critical"] = rid in crit_ids or bool(row["critical"])


def send_discord(
    webhook: str, title: str, article_body: str, *, mention_everyone: bool
) -> None:
    article_body = (article_body or "").strip()
    if article_body:
        core = f"{title}\n\n{article_body}"
    else:
        core = title
    stamp = _message_timestamp_line()
    if mention_everyone:
        text = f"{stamp}\n\n@everyone\n\n{core}"
    else:
        text = f"{stamp}\n\n{core}"
    if len(text) > 2000:
        text = text[:1997] + "..."
    payload: dict = {"content": text}
    if mention_everyone:
        # Webhooks must opt in or @everyone is suppressed / non-pinging
        payload["allowed_mentions"] = {"parse": ["everyone"]}
    _discord_spacing_wait()
    _post_discord_payload(webhook, payload)


def run_loop(webhook: str) -> None:
    seen = load_seen()
    primed = bool(seen)

    with sync_playwright() as p:
        # --no-sandbox / --disable-dev-shm-usage: required in many Linux containers (Railway/Docker).
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(
            locale="en-US",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        if BLOCK_HEAVY_RESOURCES:

            def _route_handle(route) -> None:
                if route.request.resource_type in ("image", "font", "media"):
                    route.abort()
                else:
                    route.continue_()

            context.route("**/*", _route_handle)
        page = context.new_page()

        while True:
            try:
                page.goto(FJ_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
                # Attached: feed may not be "visible" yet per Playwright; there may be zero critical rows.
                page.wait_for_selector(
                    FEED_READY, state="attached", timeout=NAV_TIMEOUT_MS
                )
                time.sleep(FEED_SETTLE_SECONDS)
                rows = parse_feed_rows(page)
                if CRITICAL_RECHECK_SECONDS > 0:
                    time.sleep(CRITICAL_RECHECK_SECONDS)
                    rows = merge_feed_rows(rows, parse_feed_rows(page))
                crit_ids = critical_news_ids_from_page(page)
                apply_critical_flags(rows, crit_ids)

                if not primed:
                    for row in rows:
                        seen.add(str(row["id"]))
                    save_seen(seen)
                    primed = True
                    logger.info(
                        "Primed: recorded %s existing headline(s); no Discord posts.",
                        len(rows),
                    )
                else:
                    for row in rows:
                        rid = str(row["id"])
                        if rid in seen:
                            continue
                        seen.add(rid)
                        save_seen(seen)
                        crit = bool(row["critical"])
                        tag = "critical" if crit else "news"
                        logger.info(
                            "New %s: %s", tag, str(row["title"])[:80]
                        )
                        try:
                            send_discord(
                                webhook,
                                str(row["title"]),
                                str(row.get("body", "")),
                                mention_everyone=crit,
                            )
                        except Exception as e:
                            logger.exception("Discord post failed: %s", e)
                            seen.discard(rid)
                            save_seen(seen)

            except Exception as e:
                logger.warning("Poll error: %s", e)

            time.sleep(POLL_SECONDS)


def main() -> int:
    webhook = resolve_webhook()
    if not webhook:
        logger.error(
            "Set NEWS_ALERTS_DISCORD_WEBHOOK_URL or DISCORD_WEBHOOK_URL, "
            "or create %s with the webhook URL.",
            WEBHOOK_FILE,
        )
        return 1
    logger.info("Polling %s every %s s", FJ_URL, POLL_SECONDS)
    run_loop(webhook)
    return 0


if __name__ == "__main__":
    sys.exit(main())
