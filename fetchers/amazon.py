import datetime
import logging
import math
import os
import random
import re
import time
from pathlib import Path
from typing import Iterable

from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from bs4.element import Tag

from core.logger import get_logger
from core.models import Item

logger = get_logger(__name__)

BASE_URL = "https://www.amazon.com"
DEBUG_DIR = Path(os.getenv("DEBUG_DIR", "/data/debug_dumps"))

# Global Amazon fetch spacing (seconds between any two wishlist page fetches)
AMAZON_MIN_SPACING = int(os.getenv("AMAZON_MIN_SPACING", "45"))
_last_amazon_fetch_ts: float = 0.0

# Per-page / retry behaviour
AMAZON_MAX_PAGES = int(os.getenv("AMAZON_MAX_PAGES", "50"))
AMAZON_MAX_PAGE_RETRIES = int(os.getenv("AMAZON_MAX_PAGE_RETRIES", "3"))
CAPTCHA_SLEEP = int(os.getenv("CAPTCHA_SLEEP", "900"))
FAIL_SLEEP = int(os.getenv("FAIL_SLEEP", "30"))
PAGE_SLEEP = int(os.getenv("PAGE_SLEEP", "5"))

DEBUG_DIR.mkdir(parents=True, exist_ok=True)


class AmazonError(Exception):
    """Generic Amazon fetch error."""


def _sanitize(name: str) -> str:
    """Normalize arbitrary wishlist names/IDs to be filesystem-safe."""
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name)


def _dump_html(wishlist_name: str | None, page_index: int, html: str) -> None:
    """Write HTML to a timestamped file when DEBUG logging is enabled."""
    if not logger.isEnabledFor(logging.DEBUG):
        return

    timestamp = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    safe = _sanitize(wishlist_name or "unknown")
    path = DEBUG_DIR / f"amazon_{safe}_page{page_index}_{timestamp}.html"
    try:
        path.write_text(html, encoding="utf-8")
        logger.debug("Dumped Amazon HTML to %s", path)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Failed to dump Amazon HTML to %s: %s", path, exc)


def normalize_wishlist_url(url: str) -> str:
    """
    Normalize various Amazon wishlist URLs to the mobile wishlist URL format.

    Examples of accepted inputs:
      - https://www.amazon.com/hz/wishlist/ls/XXXXXXXXXXXX
      - https://www.amazon.com/gp/registry/wishlist/XXXXXXXXXXXX
      - https://www.amazon.com/gp/registry/list/XXXXXXXXXXXX
    """
    m = re.search(r"/hz/wishlist/ls/([A-Za-z0-9]+)/?", url)
    if not m:
        m = re.search(r"/gp/registry/(?:wishlist|list)/([A-Za-z0-9]+)/?", url)
    if m:
        lid = m.group(1)
        return f"{BASE_URL}/gp/aw/ls?lid={lid}&ty=wishlist"
    return url


def ensure_absolute_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc:
        return url
    # treat as path on BASE_URL
    if not url.startswith("/"):
        url = "/" + url
    return f"{BASE_URL}{url}"


def looks_like_captcha_or_block(html: str) -> bool:
    """Heuristically detect Robot Check / CAPTCHA / blocked pages on mobile."""
    lower = html.lower()
    if "robot check" in lower:
        return True
    if "enter the characters you see below" in lower:
        return True
    if "/errors/validatecaptcha" in lower:
        return True
    if "to discuss automated access to amazon data" in lower:
        return True
    if "type the characters you see in this image" in lower:
        return True
    return False


def fetch_page_raw(session: requests.Session, url: str, headers: dict[str, str]) -> str:
    """Fetch a single Amazon wishlist page."""
    logger.debug("Fetching Amazon page: %s", url)
    resp = session.get(url, headers=headers, timeout=30)
    status = resp.status_code
    text = resp.text

    if status == 503:
        logger.warning(
            "Amazon returned 503 at %s (possible CAPTCHA or rate limiting).",
            url,
        )
        raise AmazonError("503 Service Unavailable")

    if status != 200:
        logger.warning("Amazon returned status %s at %s.", status, url)
        raise AmazonError(f"Bad status code {status}")

    return text


def _text_or_empty(tag: Tag | None) -> str:
    return tag.get_text(strip=True) if tag is not None else ""


def _select_first(root: Tag | BeautifulSoup, selectors: Iterable[str]) -> Tag | None:
    """Select the first tag that matches any of the given CSS selectors."""
    for sel in selectors:
        found = root.select_one(sel)
        if found is not None:
            return found
    return None


def parse_item_li(li: Tag) -> Item:
    """Parse a single wishlist item block into an Item."""
    item_id = str(li.get("id") or "")

    # Image
    image_url = ""
    img_el = li.select_one("img")
    if img_el is not None:
        src_val = img_el.get("src")
        if isinstance(src_val, str) and src_val:
            image_url = ensure_absolute_url(src_val)

    # URL
    product_url = ""
    url_el = li.select_one("a[href*='/dp/']")
    if url_el is not None:
        href_val = url_el.get("href")
        if isinstance(href_val, str) and href_val:
            # strip query for stability
            href_clean = href_val.split("?", 1)[0]
            product_url = ensure_absolute_url(href_clean)

    # Title
    title_el = _select_first(li, ["h3", "h2", ".awl-item-title", "span.a-size-base", "span.a-size-medium"])
    name = _text_or_empty(title_el) or item_id

    # Price: prefer data-price on container when present
    price_cents = -1
    currency = "USD"

    raw_price_val = li.get("data-price")
    raw_price_str = str(raw_price_val) if isinstance(raw_price_val, (str, int, float)) else None
    if raw_price_str:
        try:
            price_value = float(raw_price_str)
            if math.isfinite(price_value):
                price_cents = int(round(price_value * 100))
            else:
                logger.debug("Non-finite price %r for item %s", raw_price_str, item_id)
        except ValueError:
            logger.debug("Failed to parse data-price %r for item %s", raw_price_str, item_id)
    else:
        # Fallback to whole + fraction layout
        pw = li.select_one(".a-price-whole")
        pf = li.select_one(".a-price-fraction")
        if pw is not None:
            whole_str = _text_or_empty(pw).replace(",", "")
            frac_str = _text_or_empty(pf) if pf is not None else "00"
            try:
                whole_val = int(whole_str)
                frac_val = int(frac_str)
                price_cents = whole_val * 100 + frac_val
            except ValueError:
                logger.debug(
                    "Failed to parse price from whole=%r fraction=%r for item %s",
                    whole_str,
                    frac_str,
                    item_id,
                )

    available = price_cents >= 0

    logger.debug(
        "Parsed item: id=%s, name=%s, price_cents=%d, url=%s, image=%s",
        item_id,
        name,
        price_cents,
        product_url,
        image_url,
    )

    return Item(
        item_id=item_id,
        name=name,
        price_cents=price_cents,
        currency=currency,
        product_url=product_url,
        image_url=image_url,
        available=available,
    )


def extract_items_from_soup(soup: BeautifulSoup) -> list[Item]:
    """Extract items from Amazon mobile wishlist HTML soup."""
    containers = soup.select("li.awl-item-wrapper, li.g-item-sortable, div.g-item-sortable")
    logger.debug("Found %d Amazon item containers on page.", len(containers))

    items: list[Item] = []
    for li in containers:
        try:
            items.append(parse_item_li(li))
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Failed to parse an item block: %s", exc)
    return items


def _apply_global_spacing(wishlist_name: str | None, identifier: str, page: int) -> None:
    """Apply global Amazon fetch spacing based on AMAZON_MIN_SPACING."""
    global _last_amazon_fetch_ts
    now = time.time()
    since_last = now - _last_amazon_fetch_ts
    if since_last < AMAZON_MIN_SPACING:
        wait_for = AMAZON_MIN_SPACING - since_last
        logger.info(
            "Amazon fetch spacing: last request %.1fs ago; waiting %.1fs before fetching '%s' (page %d).",
            since_last,
            wait_for,
            wishlist_name or identifier,
            page,
        )
        time.sleep(wait_for)
    _last_amazon_fetch_ts = time.time()


def fetch_items(identifier: str, wishlist_name: str | None = None) -> list[Item]:
    """
    Fetch all items for a given Amazon wishlist using the mobile wishlist layout.

    identifier may be:
      - a full URL like https://www.amazon.com/hz/wishlist/ls/XYZ
      - a bare wishlist ID like XYZ
    """
    session = requests.Session()

    if identifier.startswith("http://") or identifier.startswith("https://"):
        first_url = normalize_wishlist_url(identifier)
    else:
        # Assume bare wishlist ID
        first_url = f"{BASE_URL}/gp/aw/ls?lid={identifier}&ty=wishlist"

    logger.info("Checking Amazon wishlist '%s' at %s", wishlist_name or identifier, first_url)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 "
            "Mobile/15A372 Safari/604.1"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.amazon.com/",
    }

    all_items: list[Item] = []
    seen_ids: set[str] = set()
    next_url: str | None = first_url
    page = 0

    while next_url is not None and page < AMAZON_MAX_PAGES:
        _apply_global_spacing(wishlist_name, identifier, page)

        current_url = next_url
        attempt = 0
        html: str | None = None

        while True:
            try:
                html = fetch_page_raw(session, current_url, headers=headers)
                if looks_like_captcha_or_block(html):
                    attempt += 1
                    if attempt >= AMAZON_MAX_PAGE_RETRIES:
                        logger.warning(
                            "CAPTCHA/robot page persisted for wishlist '%s' at %s; giving up for this run.",
                            wishlist_name or identifier,
                            current_url,
                        )
                        return all_items
                    sleep_for = random.uniform(CAPTCHA_SLEEP * 0.5, CAPTCHA_SLEEP * 1.5)
                    logger.warning(
                        "Detected CAPTCHA/robot page for wishlist '%s' at %s; sleeping %.1fs before retry (%d/%d).",
                        wishlist_name or identifier,
                        current_url,
                        sleep_for,
                        attempt,
                        AMAZON_MAX_PAGE_RETRIES,
                    )
                    time.sleep(sleep_for)
                    continue
                break
            except (requests.RequestException, AmazonError) as exc:
                attempt += 1
                if attempt >= AMAZON_MAX_PAGE_RETRIES:
                    logger.error(
                        "Failed to fetch Amazon page %s for wishlist '%s' after %d attempts: %s. "
                        "Aborting this wishlist for this run.",
                        current_url,
                        wishlist_name or identifier,
                        attempt,
                        exc,
                    )
                    return all_items
                sleep_for = random.uniform(FAIL_SLEEP * 0.5, FAIL_SLEEP * 1.5)
                logger.warning(
                    "Error fetching Amazon page %s for wishlist '%s' (attempt %d/%d): %s. "
                    "Sleeping %.1fs before retry.",
                    current_url,
                    wishlist_name or identifier,
                    attempt,
                    AMAZON_MAX_PAGE_RETRIES,
                    exc,
                    sleep_for,
                )
                time.sleep(sleep_for)
                continue

        if html is None:
            logger.error(
                "No HTML returned for %s; aborting wishlist '%s'.",
                current_url,
                wishlist_name or identifier,
            )
            return all_items

        _dump_html(wishlist_name, page, html)

        soup = BeautifulSoup(html, "html.parser")
        li_nodes = soup.select("li.awl-item-wrapper, li.g-item-sortable, div.g-item-sortable")

        if not li_nodes:
            logger.info(
                "No item containers found on Amazon wishlist '%s' page %d; ending pagination.",
                wishlist_name or identifier,
                page,
            )
            break

        page_new_count = 0
        for li in li_nodes:
            try:
                item = parse_item_li(li)
            except Exception as exc:
                logger.debug("Failed to parse item on wishlist '%s' page %d: %s", wishlist_name or identifier, page, exc)
                continue
            if item.item_id in seen_ids:
                continue
            seen_ids.add(item.item_id)
            all_items.append(item)
            page_new_count += 1

        logger.debug(
            "Amazon wishlist '%s' page %d yielded %d new items (%d total so far).",
            wishlist_name or identifier,
            page,
            page_new_count,
            len(all_items),
        )

        # If no new items on this page, stop (pre-unified behaviour)
        if page_new_count == 0:
            logger.info(
                "No new items on Amazon wishlist '%s' page %d; ending pagination.",
                wishlist_name or identifier,
                page,
            )
            break

        # Find next pagination token from mobile wishlist hidden form
        token_input = soup.select_one("form.scroll-state input.showMoreUrl")
        token_val = token_input.get("value") if token_input is not None else None

        if isinstance(token_val, str) and token_val:
            next_url = ensure_absolute_url(token_val)
            page += 1
            sleep_for = random.uniform(PAGE_SLEEP * 0.5, PAGE_SLEEP * 1.5)
            logger.debug(
                "Sleeping %.1fs before fetching next Amazon wishlist page %d for '%s'.",
                sleep_for,
                page,
                wishlist_name or identifier,
            )
            time.sleep(sleep_for)
        else:
            logger.debug(
                "No further pages (no showMoreUrl) for Amazon wishlist '%s'; pagination complete.",
                wishlist_name or identifier,
            )
            break

    logger.info(
        "Extracted %d total Amazon items for wishlist '%s'.",
        len(all_items),
        wishlist_name or identifier,
    )
    return all_items
