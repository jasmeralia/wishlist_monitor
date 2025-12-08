import os
import time
import re
import datetime
import logging
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from core.logger import get_logger
from core.models import Item

logger = get_logger(__name__)

BASE_URL = "https://www.amazon.com"

AMAZON_MIN_SPACING = int(os.getenv("AMAZON_MIN_SPACING", "45"))
_last_amazon_fetch_ts: float = 0.0
AMAZON_MAX_PAGES = int(os.getenv("AMAZON_MAX_PAGES", "50"))
MAX_PAGE_RETRIES = int(os.getenv("AMAZON_MAX_PAGE_RETRIES", "3"))
PAGE_SLEEP = int(os.getenv("PAGE_SLEEP", "5"))
CAPTCHA_SLEEP = int(os.getenv("CAPTCHA_SLEEP", "900"))
FAIL_SLEEP = int(os.getenv("FAIL_SLEEP", "30"))

# Directory for HTML debug dumps
DUMP_DIR = Path("/data/debug_dumps")
DUMP_DIR.mkdir(parents=True, exist_ok=True)


class AmazonError(Exception):
    """Generic Amazon fetch error."""
    pass


def _sanitize_filename_part(name: str) -> str:
    """Normalize arbitrary wishlist names/IDs to be filesystem-safe."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)


def _dump_html(wishlist_name: str | None, page_index: int, html: str) -> None:
    """Write HTML to a timestamped file for offline inspection (DEBUG only)."""
    ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    wl = _sanitize_filename_part(wishlist_name or "unknown")
    fname = DUMP_DIR / f"amazon_{wl}_page{page_index}_{ts}.html"
    try:
        fname.write_text(html, encoding="utf-8")
        logger.debug("Wrote Amazon HTML dump to %s", fname)
    except Exception as exc:
        logger.debug("Failed writing Amazon HTML dump %s: %s", fname, exc)


def ensure_absolute_url(url: str) -> str:
    """Return the URL as-is if it is absolute; otherwise prepend BASE_URL."""
    parsed = urlparse(url)
    if parsed.netloc:
        return url
    return f"{BASE_URL}{url}"


def parse_item_div(div) -> Item | None:
    data = div.get("data-itemid")
    if not data:
        logger.debug("Skipping div without data-itemid attribute.")
        return None

    item_id = data.strip()

    name_el = div.select_one(".a-list-item .a-link-normal")
    name = name_el.get_text(strip=True) if name_el else f"Item {item_id}"

    url_el = div.select_one(".a-list-item .a-link-normal")
    raw_href = url_el["href"] if url_el and url_el.has_attr("href") else None
    product_url = ensure_absolute_url(raw_href) if raw_href else ""

    img_el = div.select_one("img")
    raw_img = img_el["src"] if img_el and img_el.has_attr("src") else None
    image_url = raw_img or ""

    price_cents = -1
    currency = "USD"

    pw = div.select_one(".a-price-whole")
    pf = div.select_one(".a-price-fraction")

    price_whole = pw.get_text(strip=True).replace(",", "") if pw else None
    price_frac = pf.get_text(strip=True).replace(",", "") if pf else None

    if price_whole is not None:
        try:
            if price_frac:
                price_val = float(f"{price_whole}.{price_frac}")
            else:
                price_val = float(price_whole)
            price_cents = int(round(price_val * 100))
        except ValueError:
            logger.debug("Failed to parse price for item %s", item_id)

    available = price_cents >= 0

    logger.debug(
        "Parsed item: id=%s, name=%s, price_cents=%d, url=%s, image=%s, available=%s",
        item_id, name, price_cents, product_url, image_url, available
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


def fetch_page_raw(url: str) -> str:
    logger.debug("Fetching Amazon page: %s", url)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 "
            "Mobile/15A372 Safari/604.1"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    resp = requests.get(url, headers=headers, timeout=30)

    status = resp.status_code
    text = resp.text

    logger.debug("Amazon response status=%s, length=%d", status, len(text))

    if status == 503:
        raise AmazonError("503 Service Unavailable")

    if status != 200:
        raise AmazonError(f"Bad status code {status}")

    time.sleep(PAGE_SLEEP)
    return text


def looks_like_captcha_or_block(html: str) -> bool:
    lower = html.lower()
    blocked = any(
        marker in lower
        for marker in (
            "robot check",
            "enter the characters you see below",
            "/errors/validatecaptcha",
            "to discuss automated access to amazon data",
        )
    )
    if blocked:
        logger.debug("HTML appears to be a CAPTCHA/blocked page.")
    return blocked


def extract_items_from_html(html: str) -> list[Item]:
    soup = BeautifulSoup(html, "html.parser")
    divs = soup.select("div.g-item-sortable")
    logger.debug("Found %d div.g-item-sortable elements on page.", len(divs))

    items: list[Item] = []
    for div in divs:
        it = parse_item_div(div)
        if it:
            items.append(it)

    logger.debug("Extracted %d items from current page.", len(items))
    return items


def extract_next_page(html: str, current_url: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    link = soup.select_one("li.a-last a")
    if not link or not link.has_attr("href"):
        logger.debug("No next-page link found.")
        return None

    href_raw = str(link["href"])
    href = ensure_absolute_url(href_raw)

    logger.debug("Next page candidate URL: %s", href)

    if href == current_url:
        logger.warning("Next page link loops to current URL; stopping.")
        return None

    return href


def _apply_global_spacing(wishlist_name: str | None, identifier: str) -> None:
    global _last_amazon_fetch_ts
    now = time.time()
    since_last = now - _last_amazon_fetch_ts
    if since_last < AMAZON_MIN_SPACING:
        wait_for = AMAZON_MIN_SPACING - since_last
        logger.debug(
            "Amazon spacing: last request %.1fs ago; waiting %.1fs before fetching '%s'.",
            since_last,
            wait_for,
            wishlist_name or identifier,
        )
        time.sleep(wait_for)
    _last_amazon_fetch_ts = time.time()


def fetch_items(identifier: str, wishlist_name: str | None = None) -> list[Item]:
    if identifier.startswith("http://") or identifier.startswith("https://"):
        url = identifier
    else:
        url = f"{BASE_URL}/hz/wishlist/ls/{identifier}"

    logger.info("Checking Amazon wishlist '%s' at %s", wishlist_name or identifier, url)

    all_items: list[Item] = []
    next_url: str | None = url
    current_url: str | None = None
    empty_pages = 0

    for page_index in range(AMAZON_MAX_PAGES):
        if not next_url:
            logger.debug("No next URL at page %d; stopping.", page_index)
            break

        if next_url == current_url:
            logger.warning("Pagination loop detected for %s; stopping.", current_url)
            break

        current_url = next_url
        logger.debug("===== Fetching Amazon page %d: %s =====", page_index, current_url)

        _apply_global_spacing(wishlist_name, identifier)

        attempt = 0
        while True:
            try:
                html = fetch_page_raw(current_url)

                # Dump HTML only if DEBUG
                if logger.isEnabledFor(logging.DEBUG):
                    _dump_html(wishlist_name, page_index, html)

                if looks_like_captcha_or_block(html):
                    logger.warning(
                        "Detected CAPTCHA page for '%s'. Sleeping %s seconds.",
                        wishlist_name or identifier,
                        CAPTCHA_SLEEP,
                    )
                    time.sleep(CAPTCHA_SLEEP)
                    continue

                break

            except AmazonError as exc:
                attempt += 1
                logger.debug(
                    "Error fetching %s (attempt %d/%d): %s",
                    current_url, attempt, MAX_PAGE_RETRIES, exc
                )
                if attempt >= MAX_PAGE_RETRIES:
                    logger.error(
                        "Failed to fetch %s after %d attempts; aborting wishlist '%s'",
                        current_url, attempt, wishlist_name or identifier
                    )
                    return all_items
                logger.debug("Sleeping %s seconds before retry.", FAIL_SLEEP)
                time.sleep(FAIL_SLEEP)

        items = extract_items_from_html(html)
        logger.debug(
            "Page %d yielded %d items for wishlist '%s'.",
            page_index, len(items), wishlist_name or identifier
        )

        if len(items) == 0:
            empty_pages += 1
            logger.debug("Empty page detected (%d consecutive).", empty_pages)
            if empty_pages >= 2:
                logger.info(
                    "Two consecutive empty pages for '%s'; ending pagination.",
                    wishlist_name or identifier,
                )
                break
        else:
            empty_pages = 0

        all_items.extend(items)

        raw_next = extract_next_page(html, current_url)
        if not raw_next:
            logger.debug("No further next page.")
            break
        next_url = raw_next

    logger.info(
        "Extracted %d total Amazon items for wishlist '%s'.",
        len(all_items),
        wishlist_name or identifier,
    )
    return all_items
