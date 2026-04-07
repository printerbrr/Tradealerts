"""
Headless monitor for FinancialJuice home feed: posts every news item to Discord.

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
from pathlib import Path

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
POLL_SECONDS = float(os.environ.get("NEWS_ALERTS_POLL_SECONDS", "30"))
NAV_TIMEOUT_MS = int(os.environ.get("NEWS_ALERTS_NAV_TIMEOUT_MS", "60000"))
FEED_ROW = ".feedWrap"
FEED_READY = ".feedWrap"

logging.basicConfig(
    level=os.environ.get("NEWS_ALERTS_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("financialjuice_news")


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


def parse_feed_rows(page) -> list[dict[str, str | bool]]:
    """Return list of {id, title, url, critical} for each .feedWrap row."""
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
            {"id": str(nid), "title": title, "url": url, "critical": critical}
        )
    return out


def send_discord(
    webhook: str, title: str, url: str, *, mention_everyone: bool
) -> None:
    body = title
    if url:
        body = f"{title}\n{url}"
    if mention_everyone:
        body = f"@everyone\n\n{body}"
    if len(body) > 2000:
        body = body[:1997] + "..."
    payload: dict = {"content": body}
    if mention_everyone:
        payload["allowed_mentions"] = {"parse": ["everyone"]}
    r = requests.post(webhook, json=payload, timeout=30)
    r.raise_for_status()


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
        page = context.new_page()

        while True:
            try:
                page.goto(FJ_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
                # Attached: feed may not be "visible" yet per Playwright; there may be zero critical rows.
                page.wait_for_selector(
                    FEED_READY, state="attached", timeout=NAV_TIMEOUT_MS
                )
                time.sleep(1.0)
                rows = parse_feed_rows(page)

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
                                str(row["url"]),
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
