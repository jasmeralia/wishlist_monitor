import datetime
import json
import logging
import math
import os
import time
from pathlib import Path
from typing import List, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from core.logger import get_logger
from core.models import Item

logger = get_logger(__name__)

BASE_URL = "https://www.amazon.com"

# Global Amazon fetch spacing (seconds between any two wishlist page fetches)
AMAZON_MIN_SPACING = int(os.getenv("AMAZON_MIN_SPACING", "45"))
_last_amazon_fetch_ts: float = 0.0

# Per-page / retry behaviour
AMAZON_MAX_PAGES = int(os.getenv("AMAZON_MAX_PAGES", "50"))
MAX_RETRIES = int(os.getenv("AMAZON_MAX_PAGE_RETRIES", "3"))
PAGE_SLEEP = int(os.getenv("PAGE_SLEEP", "5"))
CAPTCHA_SLEEP = int(os.getenv("CAPTCHA_SLEEP", "900"))
FAIL_SLEEP = int(os.getenv("FAIL_SLEEP", "30"))

DUMP_DIR = Path("/data/debug_dumps")
DUMP_DIR.mkdir(parents=True, exist_ok=True)


class AmazonError(Exception):
    """Generic Amazon fetch error."""


def _sanitize(name: str) -> str:
    """Normalize arbitrary wishlist names/IDs to be filesystem-safe."""
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name)


def _dump_html(wishlist_name: Optional[str], page_index: int, html: str) -> None:
    """Write HTML to a timestamped file when DEBUG logging is enabled."""
    if not logger.isEnabledFor(logging.DEBUG):
        return

    timestamp = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    safe = _sanitize(wishlist_name or "unknown")
    path = DUMP_DIR / f"amazon_{safe}_page{page_index}_{timestamp}.html"
    try:
        path.write_text(html, encoding="utf-8")
        logger.debug("Dumped Amazon HTML to %s", path)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Failed to dump Amazon HTML to %s: %s", path, exc)


def ensure_absolute_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc:
        return url
    return f"{BASE_URL}{url}"


def looks_like_captcha(html: str) -> bool:
    """Detect Robot Check / CAPTCHA / automated-access blocking pages."""
    lower_html = html.lower()
    blocked = any(
        marker in lower_html
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


def fetch_page(url: str) -> str:
    """Fetch a single Amazon wishlist page (mobile-style layout)."""
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
    if status != 200:
        logger.debug("Amazon response status=%s, length=%d", status, len(resp.text))
        raise AmazonError(f"Bad status {status}")
    return resp.text


def parse_item_li(li) -> Item:
    """Parse a single wishlist item block into an Item."""
    item_id = str(li.get("id") or "")

    # Image
    img_el = li.select_one("img")
    image_url = img_el["src"] if img_el is not None and img_el.has_attr("src") else ""
    if image_url:
        image_url = ensure_absolute_url(image_url)

    # URL
    url_el = li.select_one("a[href*='/dp/']")
    product_url = ""
    if url_el is not None and url_el.has_attr("href"):
        product_url = ensure_absolute_url(url_el["href"])

    # Title
    title_el = li.select_one("h3, h2, span.a-size-base")
    name = title_el.get_text(strip=True) if title_el is not None else item_id

    # Price: prefer data-price on container when present
    price_cents = -1
    currency = "USD"

    raw_price = li.get("data-price")
    if raw_price is not None:
        try:
            price_value = float(str(raw_price))
            if math.isfinite(price_value):
                price_cents = int(round(price_value * 100))
            else:
                logger.debug("Non-finite price %r for item %s", raw_price, item_id)
        except ValueError:
            logger.debug("Failed to parse data-price %r for item %s", raw_price, item_id)
    else:
        # Fallback to whole + fraction layout
        pw = li.select_one(".a-price-whole")
        pf = li.select_one(".a-price-fraction")

        if pw is not None:
            whole_str = pw.get_text(strip=True).replace(",", "")
            frac_str = pf.get_text(strip=True) if pf is not None else "00"
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


def extract_items(html: str) -> List[Item]:
    """Extract all wishlist items from the given HTML."""
    soup = BeautifulSoup(html, "html.parser")
    li_items = soup.select("li.awl-item-wrapper, li.g-item-sortable")
    logger.debug("Found %d Amazon item container <li> elements", len(li_items))

    items: List[Item] = []
    for li in li_items:
        try:
            item = parse_item_li(li)
            items.append(item)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Failed to parse an item block: %s", exc)
    return items


def _extract_pagination_url(html: str, current_url: str) -> Optional[str]:
    """Extract the next-page URL, if any.

    Prefer `showMoreUrl` from <script type="a-state"> JSON; fall back to
    lastEvaluatedKey/itemsRenderedSoFar hidden inputs when present.
    """
    soup = BeautifulSoup(html, "html.parser")

    # First, try script[type="a-state"] with showMoreUrl
    scripts = soup.find_all("script", attrs={"type": "a-state"})
    for script in scripts:
        raw = script.get_text(strip=True)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        show_more = data.get("showMoreUrl")
        if show_more:
            show_more_str = str(show_more)
            # Absolute or relative path
            next_url = ensure_absolute_url(show_more_str)
            logger.debug("Pagination via showMoreUrl: %s", next_url)
            return next_url

    # Fallback: hidden lastEvaluatedKey/itemsRenderedSoFar
    lek_el = soup.select_one("input.lastEvaluatedKey")
    rendered_el = soup.select_one("input.itemsRenderedSoFar")
    if lek_el is not None and rendered_el is not None:
        raw_token = lek_el.get("value")
        raw_rendered = rendered_el.get("value")

        token = str(raw_token).strip() if raw_token is not None else ""
        rendered = str(raw_rendered).strip() if raw_rendered is not None else ""

        if token and rendered:
            parsed = urlparse(current_url)
            query = dict(parse_qsl(parsed.query, keep_blank_values=True))
            query["lastEvaluatedKey"] = token
            query["itemsRenderedSoFar"] = rendered
            new_query = urlencode(query, doseq=True)
            new_parsed = parsed._replace(query=new_query)
            next_url = urlunparse(new_parsed)
            logger.debug(
                "Pagination via lastEvaluatedKey/itemsRenderedSoFar: %s", next_url
            )
            return next_url

    logger.debug("No pagination token found on page; assuming last page.")
    return None


def _apply_spacing(wishlist_name: Optional[str]) -> None:
    """Enforce minimum spacing between Amazon fetches across wishlists."""
    global _last_amazon_fetch_ts
    now = time.time()
    elapsed = now - _last_amazon_fetch_ts
    if elapsed < AMAZON_MIN_SPACING:
        wait_time = AMAZON_MIN_SPACING - elapsed
        logger.debug(
            "Amazon fetch spacing: last request %.1fs ago; waiting %.1fs before '%s'.",
            elapsed,
            wait_time,
            wishlist_name or "<unknown>",
        )
        time.sleep(wait_time)
    _last_amazon_fetch_ts = time.time()


def fetch_items(identifier: str, wishlist_name: Optional[str] = None) -> List[Item]:
    """
    Fetch all items for a given Amazon wishlist (mobile version).

    identifier may be:
      - a full URL like https://www.amazon.com/hz/wishlist/ls/XYZ
      - a bare wishlist ID like XYZ
    """
    if identifier.startswith("http://") or identifier.startswith("https://"):
        url = identifier
    else:
        url = f"{BASE_URL}/hz/wishlist/ls/{identifier}"

    logger.info("Checking Amazon wishlist '%s' at %s", wishlist_name or identifier, url)

    all_items: List[Item] = []
    next_url: Optional[str] = url
    current_url: Optional[str] = None
    empty_pages = 0

    for page_index in range(AMAZON_MAX_PAGES):
        if not next_url:
            logger.debug("No next URL at page %d; stopping pagination.", page_index)
            break

        if next_url == current_url:
            logger.warning("Pagination loop detected at %s; stopping.", current_url)
            break

        current_url = next_url
        _apply_spacing(wishlist_name)

        attempt = 0
        html: Optional[str] = None
        while True:
            try:
                html = fetch_page(current_url)
                if looks_like_captcha(html):
                    logger.warning(
                        "CAPTCHA/robot page detected for wishlist '%s'; "
                        "sleeping %s seconds before retry.",
                        wishlist_name or identifier,
                        CAPTCHA_SLEEP,
                    )
                    time.sleep(CAPTCHA_SLEEP)
                    # Do NOT count against MAX_RETRIES; just retry
                    continue

                # We have a plausible HTML page; proceed
                break

            except AmazonError as exc:
                attempt += 1
                logger.warning(
                    "Error fetching %s for wishlist '%s' (attempt %d/%d): %s",
                    current_url,
                    wishlist_name or identifier,
                    attempt,
                    MAX_RETRIES,
                    exc,
                )
                if attempt >= MAX_RETRIES:
                    logger.error(
                        "Failed to fetch %s after %d attempts; aborting wishlist '%s'.",
                        current_url,
                        attempt,
                        wishlist_name or identifier,
                    )
                    return all_items
                logger.debug("Sleeping %s seconds before retry.", FAIL_SLEEP)
                time.sleep(FAIL_SLEEP)

        if html is None:
            logger.error("No HTML returned for %s; aborting wishlist '%s'.", current_url, wishlist_name or identifier)
            return all_items

        _dump_html(wishlist_name, page_index, html)

        page_items = extract_items(html)
        logger.debug(
            "Page %d yielded %d items for wishlist '%s'.",
            page_index,
            len(page_items),
            wishlist_name or identifier,
        )

        if not page_items:
            empty_pages += 1
            logger.debug("Empty Amazon page detected (%d consecutive).", empty_pages)
            if empty_pages >= 2:
                logger.info(
                    "Two consecutive empty pages for wishlist '%s'; ending pagination.",
                    wishlist_name or identifier,
                )
                break
        else:
            empty_pages = 0

        all_items.extend(page_items)

        # Sleep before the *next* page fetch, not before dumping/parsing
        if PAGE_SLEEP > 0:
            logger.debug(
                "PAGE_SLEEP: sleeping %s seconds before next Amazon request",
                PAGE_SLEEP,
            )
            time.sleep(PAGE_SLEEP)

        next_url = _extract_pagination_url(html, current_url)

    logger.info(
        "Extracted %d total Amazon items for wishlist '%s'.",
        len(all_items),
        wishlist_name or identifier,
    )
    return all_items
