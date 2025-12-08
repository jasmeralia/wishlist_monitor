# monitor.py
import os
import json
import time
import random
from typing import Any, Dict, List, Tuple

from core.logger import get_logger
from core import storage
from core.diff import diff_items
from core.report_html import build_html_report, build_plaintext_report
from core.emailer import send_email, get_global_recipients
from fetchers import FETCHERS

logger = get_logger(__name__)

POLL_MINUTES = int(os.getenv("POLL_MINUTES", "10"))
MODE = os.getenv("MODE", "daemon").lower()  # "daemon" or "once"
CONFIG_PATH = os.getenv("CONFIG_PATH", "/data/config.json")


def jitter_sleep_minutes(minutes: int):
    base = max(1, minutes)
    jitter = random.uniform(-0.1 * base, 0.1 * base)
    total = base + jitter
    logger.info("Sleeping %.1f minutes before next cycle.", total)
    time.sleep(total * 60)


def load_config(path: str = CONFIG_PATH) -> Dict[str, Any]:
    if not os.path.exists(path):
        logger.error("Config file not found at %s", path)
        raise SystemExit(1)
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        logger.error("Failed to load config.json at %s: %s", path, e)
        raise SystemExit(1)

    if not isinstance(cfg, dict) or "wishlists" not in cfg:
        logger.error("config.json must be an object with a 'wishlists' key.")
        raise SystemExit(1)

    if not isinstance(cfg["wishlists"], list) or not cfg["wishlists"]:
        logger.error("config.json 'wishlists' must be a non-empty list.")
        raise SystemExit(1)

    return cfg


def get_recipients_for_wishlist(wl: Dict[str, Any]) -> List[str]:
    # Explicit per-wishlist recipients
    wl_recipients = wl.get("recipients")
    if isinstance(wl_recipients, list):
        cleaned = [r.strip() for r in wl_recipients if isinstance(r, str) and r.strip()]
        if cleaned:
            return cleaned

    # Fallback to global recipients
    global_recipients = get_global_recipients()
    return global_recipients


def process_wishlist(wl: Dict[str, Any]):
    platform = wl.get("platform", "").strip().lower()
    name = wl.get("name", "").strip()
    identifier = wl.get("identifier", "").strip()
    enabled = wl.get("enabled", True)

    if not platform or not name or not identifier:
        logger.error("Invalid wishlist entry (missing platform/name/identifier): %s", wl)
        return

    if not enabled:
        logger.info("Wishlist '%s' (%s) is disabled; skipping.", name, platform)
        return

    fetcher = FETCHERS.get(platform)
    if not fetcher:
        logger.error(
            "No fetcher registered for platform '%s'; skipping wishlist '%s'.",
            platform,
            name,
        )
        return

    wishlist_id = identifier  # DB key; can be URL or username

    logger.info(
        "Processing wishlist: platform=%s, name=%s, identifier=%s",
        platform,
        name,
        identifier,
    )

    previous_items = storage.get_previous_items(platform, wishlist_id)
    previous_count = len(previous_items)

    items = fetcher(identifier, name)
    if items is None or len(items) == 0:
        if previous_count > 0:
            logger.error(
                "Fetch returned zero items for %s:%s but previous count is %d; "
                "treating as transient failure and skipping diff.",
                platform,
                wishlist_id,
                previous_count,
            )
        else:
            logger.info(
                "Fetch returned zero items for %s:%s and there are no previous items; skipping diff.",
                platform,
                wishlist_id,
            )
        return

    added, removed, price_changes = diff_items(previous_items, items)
    new_count = len(items)

    # Always persist current snapshot (to update last_seen etc.)
    storage.save_items_and_events(
        platform, wishlist_id, items, added, removed, price_changes
    )

    if not (added or removed or price_changes):
        logger.info(
            "No changes for %s '%s' (%s).", platform, name, wishlist_id
        )
        return

    subject = f"[{platform.capitalize()}] Changes detected for {name}"
    html_body = build_html_report(
        platform,
        name,
        wishlist_id,
        added,
        removed,
        price_changes,
        previous_count,
        new_count,
    )
    text_body = build_plaintext_report(
        platform,
        name,
        wishlist_id,
        added,
        removed,
        price_changes,
        previous_count,
        new_count,
    )

    recipients = get_recipients_for_wishlist(wl)
    if not recipients:
        logger.error(
            "No recipients defined for wishlist '%s' (platform=%s); "
            "EMAIL_TO env is empty and no 'recipients' set in config. Skipping email.",
            name,
            platform,
        )
        return

    send_email(subject, html_body, text_body, recipients)


def run_once():
    storage.ensure_db()
    cfg = load_config()
    wishlists = cfg.get("wishlists", [])
    random.shuffle(wishlists)  # Option B randomization
    for wl in wishlists:
        try:
            process_wishlist(wl)
        except Exception as e:
            logger.exception("Unhandled error processing wishlist %s: %s", wl, e)
    return 0


def run_daemon():
    logger.info("Starting daemon; poll every %d minutes (global base).", POLL_MINUTES)
    storage.ensure_db()
    last_run_map: dict[Tuple[str, str], float] = {}  # (platform, name) -> last_ts

    while True:
        try:
            cfg = load_config()
            wishlists = cfg.get("wishlists", [])
            now = time.time()

            for wl in wishlists:
                platform = wl.get("platform", "").strip().lower()
                name = wl.get("name", "").strip()
                if not platform or not name:
                    logger.error("Invalid wishlist entry (missing platform/name): %s", wl)
                    continue

                key = (platform, name)

                # Per-wishlist poll override (integers only)
                poll_val = wl.get("poll_minutes")
                if poll_val is None:
                    poll_minutes = POLL_MINUTES
                else:
                    try:
                        poll_minutes = int(poll_val)
                    except (TypeError, ValueError):
                        logger.warning(
                            "Invalid poll_minutes '%s' for wishlist '%s'; "
                            "falling back to global POLL_MINUTES=%d.",
                            poll_val,
                            name,
                            POLL_MINUTES,
                        )
                        poll_minutes = POLL_MINUTES

                if poll_minutes < 1:
                    poll_minutes = 1

                last_ts = last_run_map.get(key)
                if last_ts is not None:
                    elapsed = (now - last_ts) / 60.0
                    if elapsed < poll_minutes:
                        logger.debug(
                            "Skipping wishlist '%s' (%s); last run %.1f min ago, "
                            "poll_minutes=%d.",
                            name,
                            platform,
                            elapsed,
                            poll_minutes,
                        )
                        continue

                try:
                    process_wishlist(wl)
                except Exception as e:
                    logger.exception(
                        "Unhandled error processing wishlist %s (platform=%s): %s",
                        name,
                        platform,
                        e,
                    )
                finally:
                    last_run_map[key] = time.time()

        except Exception as e:
            logger.exception("Unhandled error in daemon loop: %s", e)

        jitter_sleep_minutes(POLL_MINUTES)


if __name__ == "__main__":
    try:
        if MODE == "once":
            exit(run_once())
        else:
            run_daemon()
    except Exception as e:
        logger.exception("Fatal error in monitor: %s", e)
        exit(2)
