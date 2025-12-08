import datetime
import logging
import os
import re
import time
import json
from pathlib import Path
from urllib.parse import parse_qsl, urlparse, urlunparse, urlencode

import requests
from bs4 import BeautifulSoup

from core.logger import get_logger
from core.models import Item

logger = get_logger(__name__)

BASE_URL = "https://www.amazon.com"

AMAZON_MIN_SPACING = int(os.getenv("AMAZON_MIN_SPACING", "45"))
_last_fetch: float = 0.0
AMAZON_MAX_PAGES = int(os.getenv("AMAZON_MAX_PAGES", "50"))
MAX_RETRIES = int(os.getenv("AMAZON_MAX_PAGE_RETRIES", "3"))
PAGE_SLEEP = int(os.getenv("PAGE_SLEEP", "5"))
CAPTCHA_SLEEP = int(os.getenv("CAPTCHA_SLEEP", "900"))
FAIL_SLEEP = int(os.getenv("FAIL_SLEEP", "30"))

DUMP_DIR = Path("/data/debug_dumps")
DUMP_DIR.mkdir(parents=True, exist_ok=True)


class AmazonError(Exception):
    """Generic Amazon fetch error."""

    pass


def _sanitize(name: str) -> str:
    """Normalize arbitrary wishlist names/IDs to be filesystem-safe."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)


def _dump_html(wishlist_name: str | None, page_index: int, html: str) -> None:
    """Write HTML to a timestamped file when DEBUG logging is enabled."""
    if not logger.isEnabledFor(logging.DEBUG):
        return

    timestamp = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    safe_name = _sanitize(wishlist_name or "unknown")
    path = DUMP_DIR / f"amazon_{safe_name}_page{page_index}_{timestamp}.html"
    try:
        path.write_text(html, encoding="utf-8")
        logger.debug("Dumped Amazon HTML to %s", path)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Failed to dump Amazon HTML to %s: %s", path, exc)


def ensure_absolute_url(url: str) -> str:
    """Return the URL as-is if it is absolute; otherwise prepend BASE_URL."""
    parsed = urlparse(url)
    if parsed.netloc:
        return url
    return f"{BASE_URL}{url}"


def parse_item_li(li) -> Item | None:
    """Parse a single mobile wishlist <li.awl-item-wrapper> into an Item."""
    item_id = li.get("id") or ""

    img_el = li.select_one("div.awl-item-image img")
    image_url = img_el["src"] if img_el and img_el.has_attr("src") else ""

    link_el = li.select_one("a[href*='/dp/']")
    href = link_el["href"] if link_el and link_el.has_attr("href") else ""
    product_url = ensure_absolute_url(href) if href else ""

    title_el = li.select_one("h3.awl-item-title")
    name = title_el.get_text(strip=True) if title_el else item_id

    price_cents = -1
    raw_price = li.get("data-price")
    if raw_price:
        try:
            price_value = float(raw_price)
            price_cents = int(round(price_value * 100))
        except ValueError:
            logger.debug("Failed to parse data-price %r for item %s", raw_price, item_id)

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
        currency="USD",
        product_url=product_url,
        image_url=image_url,
        available=price_cents >= 0,
    )


def fetch_page(url: str) -> str:
    """Fetch a single Amazon wishlist page (mobile layout)."""
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
    time.sleep(PAGE_SLEEP)
    return resp.text


def looks_like_captcha(html: str) -> bool:
    """Detect Robot Check / CAPTCHA / automated-access blocking pages."""
    lower_html = html.lower()
    blocked = any(
        marker in lower_html
        for marker in [
            "robot check",
            "enter the characters you see below",
            "/errors/validatecaptcha",
            "to discuss automated access to amazon data",
        ]
    )
    if blocked:
        logger.debug("HTML appears to be a CAPTCHA/blocked page.")
    return blocked


def extract_items(html: str) -> list[Item]:
    """Extract all wishlist items from the given HTML."""
    soup = BeautifulSoup(html, "html.parser")
    li_items = soup.select("li.awl-item-wrapper")
    logger.debug("Found %d li.awl-item-wrapper elements", len(li_items))

    items: list[Item] = []
    for li in li_items:
        item = parse_item_li(li)
        if item:
            items.append(item)
    return items


def _extract_pagination_tokens(html: str) -> tuple[str | None, str | None]:
    """Extract pagination token from Amazon mobile wishlist a-state JSON.

    We look for <script type="a-state"> blocks whose JSON contains
    a "showMoreUrl" field. That URL already encodes pagination state.
    """
    soup = BeautifulSoup(html, "html.parser")
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
            logger.debug("Found showMoreUrl for pagination: %s", show_more)
            # We only really care about the URL; the second value is kept for type compatibility.
            return str(show_more), "1"
    return None, None


def _build_next_url(current_url: str, token: str, rendered: str) -> str:
    """Build the next-page URL.

    If `token` looks like a URL/path (from showMoreUrl), treat it directly
    as the next URL (relative or absolute). Otherwise, fall back to the
    legacy query-parameter approach using lastEvaluatedKey/itemsRenderedSoFar.
    """
    # Primary path: token is a URL or path (from showMoreUrl)
    if token.startswith("http://") or token.startswith("https://") or token.startswith("/"):
        next_url = ensure_absolute_url(token)
        logger.debug("Next page URL (showMoreUrl) built as: %s", next_url)
        return next_url

    # Fallback: legacy behavior using query parameters
    parsed = urlparse(current_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["lastEvaluatedKey"] = token
    query["itemsRenderedSoFar"] = rendered
    new_query = urlencode(query, doseq=True)
    new_parsed = parsed._replace(query=new_query)
    next_url = urlunparse(new_parsed)
    logger.debug("Next page URL (legacy token) built as: %s", next_url)
    return next_url


def _apply_spacing(wishlist_name: str | None) -> None:
    """Enforce minimum spacing between Amazon fetches."""
    global _last_fetch
    now = time.time()
    elapsed = now - _last_fetch
    if elapsed < AMAZON_MIN_SPACING:
        wait_time = AMAZON_MIN_SPACING - elapsed
        logger.debug(
            "Amazon fetch spacing: last request %.1fs ago; waiting %.1fs before '%s'.",
            elapsed,
            wait_time,
            wishlist_name or "<unknown>",
        )
        time.sleep(wait_time)
    _last_fetch = time.time()


def fetch_items(identifier: str, wishlist_name: str | None = None) -> list[Item]:
    """Fetch all items for a given Amazon wishlist (mobile version).

    identifier may be:
      - a full URL like https://www.amazon.com/hz/wishlist/ls/XYZ
      - a bare wishlist ID like XYZ
    """
    if identifier.startswith("http://") or identifier.startswith("https://"):
        url = identifier
    else:
        url = f"{BASE_URL}/hz/wishlist/ls/{identifier}"

    logger.info("Fetching Amazon wishlist '%s' at %s", wishlist_name or identifier, url)

    all_items: list[Item] = []
    next_url: str | None = url
    current_url: str | None = None
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
        while True:
            try:
                html = fetch_page(current_url)
                _dump_html(wishlist_name, page_index, html)

                if looks_like_captcha(html):
                    logger.warning(
                        "CAPTCHA/robot page detected for wishlist '%s'; "
                        "sleeping %s seconds before retry.",
                        wishlist_name or identifier,
                        CAPTCHA_SLEEP,
                    )
                    time.sleep(CAPTCHA_SLEEP)
                    continue

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

        page_items = extract_items(html)
        logger.debug(
            "Page %d yielded %d items for wishlist '%s'.",
            page_index,
            len(page_items),
            wishlist_name or identifier,
        )

        if not page_items:
            empty_pages += 1
            logger.debug("Empty page detected (%d consecutive).", empty_pages)
            if empty_pages >= 2:
                logger.info(
                    "Two consecutive empty pages for wishlist '%s'; ending pagination.",
                    wishlist_name or identifier,
                )
                break
        else:
            empty_pages = 0

        all_items.extend(page_items)

        token, rendered = _extract_pagination_tokens(html)
        if not token or not rendered:
            logger.debug(
                "No pagination token found on page %d for wishlist '%s'; "
                "assuming last page.",
                page_index,
                wishlist_name or identifier,
            )
            break

        next_url = _build_next_url(current_url, token, rendered)

    logger.info(
        "Extracted %d total Amazon items for wishlist '%s'.",
        len(all_items),
        wishlist_name or identifier,
    )
    return all_items
