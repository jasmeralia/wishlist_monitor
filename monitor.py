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


def jitter_sleep_minutes(minutes: int) -> None:
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
            cfg: Dict[str, Any] = json.load(f)
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
    wl_recipients = wl.get("recipients")
    if isinstance(wl_recipients, list):
        cleaned = [r.strip() for r in wl_recipients if isinstance(r, str) and r.strip()]
        if cleaned:
            return cleaned
    return get_global_recipients()


def process_wishlist(wl: Dict[str, Any]) -> None:
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
            platform, name,
        )
        return

    wishlist_id = identifier
    logger.info(
        "Processing wishlist: platform=%s, name=%s, identifier=%s",
        platform, name, identifier,
    )

    previous_items = storage.get_previous_items(platform, wishlist_id)
    previous_count = len(previous_items)

    items = fetcher(identifier, name)
    if not items:
        if previous_count > 0:
            logger.error(
                "Fetch returned zero items for %s:%s but previous count is %d; skipping diff.",
                platform, wishlist_id, previous_count,
            )
        else:
            logger.info(
                "Fetch returned zero items for %s:%s and no previous items; skipping.",
                platform, wishlist_id,
            )
        return

    added, removed, price_changes = diff_items(previous_items, items)
    new_count = len(items)

    storage.save_items_and_events(
        platform, wishlist_id, items, added, removed, price_changes
    )

    if not (added or removed or price_changes):
        logger.info("No changes for %s '%s' (%s).", platform, name, wishlist_id)
        return

    subject = f"[Wishlist Monitor] Changes detected on {platform.capitalize()} for {name}"
    html_body = build_html_report(
        platform, name, wishlist_id, added, removed, price_changes, previous_count, new_count
    )
    text_body = build_plaintext_report(
        platform, name, wishlist_id, added, removed, price_changes, previous_count, new_count
    )

    recipients = get_recipients_for_wishlist(wl)
    if not recipients:
        logger.error("No recipients for wishlist '%s' (platform=%s).", name, platform)
        return

    send_email(subject, html_body, text_body, recipients)


def _wishlist_debug_id(wl: Dict[str, Any]) -> str:
    platform = wl.get("platform", "").strip().lower()
    name = wl.get("name", "").strip()
    if not platform and not name:
        return "<invalid>"
    return f"{platform}:{name}"


def _debug_log_wishlist_order(phase: str, wishlists: List[Dict[str, Any]]) -> None:
    try:
        order = [_wishlist_debug_id(wl) for wl in wishlists if isinstance(wl, dict)]
    except Exception:
        order = ["<error>"]
    logger.debug("%s wishlist order: %s", phase, order)


def run_once() -> int:
    storage.ensure_db()
    cfg = load_config()
    wishlists = cfg.get("wishlists", [])

    _debug_log_wishlist_order("run_once BEFORE shuffle", wishlists)
    random.shuffle(wishlists)
    _debug_log_wishlist_order("run_once AFTER shuffle", wishlists)

    for wl in wishlists:
        try:
            process_wishlist(wl)
        except Exception as e:
            logger.exception("Unhandled error in run_once: %s", e)

    return 0


def run_daemon() -> None:
    logger.info("Starting daemon; poll every %d minutes.", POLL_MINUTES)
    storage.ensure_db()
    last_run_map: Dict[Tuple[str, str], float] = {}

    while True:
        try:
            cfg = load_config()
            wishlists = cfg.get("wishlists", [])
            now = time.time()

            seed = time.time_ns()
            random.seed(seed)
            logger.debug(
                "Daemon cycle start %s with seed %d (%d wishlists).",
                time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)),
                seed, len(wishlists),
            )

            _debug_log_wishlist_order("daemon BEFORE shuffle", wishlists)
            random.shuffle(wishlists)
            _debug_log_wishlist_order("daemon AFTER shuffle", wishlists)

            logger.debug(
                "Daemon processing order: %s",
                [_wishlist_debug_id(wl) for wl in wishlists],
            )

            for wl in wishlists:
                if not isinstance(wl, dict):
                    logger.error("Invalid WL entry: %s", wl)
                    continue

                platform = wl.get("platform", "").strip().lower()
                name = wl.get("name", "").strip()
                if not platform or not name:
                    logger.error("Invalid WL (missing platform or name): %s", wl)
                    continue

                key = (platform, name)
                poll_val = wl.get("poll_minutes")

                try:
                    poll_minutes = int(poll_val) if poll_val is not None else POLL_MINUTES
                except Exception:
                    poll_minutes = POLL_MINUTES

                poll_minutes = max(1, poll_minutes)

                last_ts = last_run_map.get(key)
                if last_ts:
                    elapsed = (now - last_ts) / 60
                    if elapsed < poll_minutes:
                        logger.debug(
                            "Skip %s:%s (%.1f < %d minutes).",
                            platform, name, elapsed, poll_minutes,
                        )
                        continue

                logger.debug(
                    "Processing WL %s:%s (poll_minutes=%d).",
                    platform, name, poll_minutes,
                )

                try:
                    process_wishlist(wl)
                except Exception as e:
                    logger.exception("Error processing %s:%s: %s", platform, name, e)
                finally:
                    last_run_map[key] = time.time()

        except Exception as e:
            logger.exception("Unhandled error in daemon loop: %s", e)

        jitter_sleep_minutes(POLL_MINUTES)


if __name__ == "__main__":
    try:
        if MODE == "once":
            raise SystemExit(run_once())
        else:
            run_daemon()
    except Exception as e:
        logger.exception("Fatal monitor error: %s", e)
        raise SystemExit(2)
