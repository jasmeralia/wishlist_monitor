import os
import time
import requests
from typing import Optional
from urllib.parse import urlparse
from bs4 import BeautifulSoup, Tag
from core.logger import get_logger
from core.models import Item
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = get_logger(__name__)

BASE_URL = "https://www.amazon.com"

# AMAZON_MIN_SPACING: minimum delay between any two Amazon fetches (in seconds)
AMAZON_MIN_SPACING = int(os.getenv("AMAZON_MIN_SPACING", "45"))
_last_amazon_fetch_ts = 0.0

# Maximum pages to paginate
AMAZON_MAX_PAGES = int(os.getenv("AMAZON_MAX_PAGES", "50"))

# Retry logic
MAX_RETRIES = int(os.getenv("AMAZON_MAX_RETRIES", "3"))
RETRY_MULTIPLIER = int(os.getenv("AMAZON_RETRY_MULTIPLIER", "2"))
RETRY_MIN = int(os.getenv("AMAZON_RETRY_MIN", "1"))
RETRY_MAX = int(os.getenv("AMAZON_RETRY_MAX", "10"))

# Sleep values when encountering CAPTCHA or fetch failures
PAGE_SLEEP = int(os.getenv("PAGE_SLEEP", "5"))
CAPTCHA_SLEEP = int(os.getenv("CAPTCHA_SLEEP", "600"))
FAIL_SLEEP = int(os.getenv("FAIL_SLEEP", "30"))


class AmazonError(Exception):
    pass


def ensure_absolute_url(url: str) -> str:
    """Return the URL as-is if it is absolute; otherwise prepend BASE_URL."""
    parsed = urlparse(url)
    if parsed.netloc:
        return url
    return f"{BASE_URL}{url}"


def parse_item_div(div: Tag) -> Optional[Item]:
    """Parse a single Amazon item div and return an Item object."""
    data = div.get("data-itemid")
    if not data or not isinstance(data, str):
        logger.debug("Skipping div without data-itemid")
        return None

    item_id = data.strip()
    name_el = div.select_one(".a-list-item .a-link-normal")
    name = name_el.get_text(strip=True) if name_el else f"Item {item_id}"

    url_el = div.select_one(".a-list-item .a-link-normal")
    href_raw = url_el.get("href") if url_el else None
    product_url = ensure_absolute_url(href_raw) if isinstance(href_raw, str) else ""

    img_el = div.select_one("img")
    src_raw = img_el.get("src") if img_el else None
    image_url = src_raw if isinstance(src_raw, str) else ""

    price_cents = -1
    currency = "USD"

    price_whole = None
    price_frac = None

    price_span = div.select_one(".a-price-whole")
    frac_span = div.select_one(".a-price-fraction")

    if price_span:
        price_whole = price_span.get_text(strip=True).replace(",", "")
    if frac_span:
        price_frac = frac_span.get_text(strip=True).replace(",", "")

    if price_whole is not None:
        try:
            if price_frac:
                price_val = float(f"{price_whole}.{price_frac}")
            else:
                price_val = float(price_whole)
            price_cents = int(round(price_val * 100))
        except ValueError:
            pass

    available = price_cents >= 0

    return Item(
        item_id=item_id,
        name=name,
        price_cents=price_cents,
        currency=currency,
        product_url=product_url,
        image_url=image_url,
        available=available,
    )


@retry(
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=RETRY_MIN, max=RETRY_MAX),
    retry=retry_if_exception_type(AmazonError),
)
def fetch_page(url: str) -> str:
    """Fetch a single Amazon wishlist page with retry and CAPTCHA detection."""
    logger.debug("Fetching page URL: %s", url)

    # Basic user agent spoofing
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Safari/605.1.15"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    resp = requests.get(url, headers=headers, timeout=30)

    if resp.status_code == 503 or "captcha" in resp.text.lower():
        logger.warning("CAPTCHA encountered at %s. Sleeping for %s seconds.", url, CAPTCHA_SLEEP)
        time.sleep(CAPTCHA_SLEEP)
        raise AmazonError("CAPTCHA encountered")

    if resp.status_code != 200:
        logger.warning("Amazon returned non-200 (%s) at %s; sleeping %s seconds",
                       resp.status_code, url, FAIL_SLEEP)
        time.sleep(FAIL_SLEEP)
        raise AmazonError(f"Bad status code {resp.status_code}")

    time.sleep(PAGE_SLEEP)
    return resp.text


def extract_items_from_html(html: str) -> list[Item]:
    """Extract items from Amazon mobile wishlist HTML."""
    soup = BeautifulSoup(html, "lxml")

    divs = soup.select("div.g-item-sortable")
    items = []

    for div in divs:
        if not isinstance(div, Tag):
            continue
        item = parse_item_div(div)
        if item:
            items.append(item)

    return items


def extract_next_page(html: str) -> Optional[str]:
    """Return URL of next wishlist page, or None if none exists."""
    soup = BeautifulSoup(html, "lxml")
    link = soup.select_one("li.a-last a")
    if link:
        href_raw = link.get("href")
        if isinstance(href_raw, str):
            return ensure_absolute_url(href_raw)
    return None


def fetch_items(identifier: str, wishlist_name: Optional[str] = None) -> list[Item]:
    """
    Fetch and return the items for an Amazon wishlist.

    identifier may be:
      - a full URL like https://www.amazon.com/hz/wishlist/ls/XYZ
      - a bare wishlist ID like XYZ
    """
    global _last_amazon_fetch_ts

    if identifier.startswith("http://") or identifier.startswith("https://"):
        url = identifier
    else:
        url = f"{BASE_URL}/hz/wishlist/ls/{identifier}"

    logger.info("Checking Amazon wishlist '%s' at %s", wishlist_name or identifier, url)

    # ----------------------------------------------------------------------
    # GLOBAL AMAZON FETCH SPACING (Option E)
    # ----------------------------------------------------------------------
    now = time.time()
    since_last = now - _last_amazon_fetch_ts
    if since_last < AMAZON_MIN_SPACING:
        wait_for = AMAZON_MIN_SPACING - since_last
        logger.info(
            "Amazon fetch spacing: last request %.1fs ago; waiting %.1fs before fetching '%s'.",
            since_last,
            wait_for,
            wishlist_name or identifier,
        )
        time.sleep(wait_for)

    # Mark timestamp *before* making the request (prevents successive retries compressing together)
    _last_amazon_fetch_ts = time.time()
    # ----------------------------------------------------------------------

    all_items: list[Item] = []
    next_url: Optional[str] = url

    current_url = None
    empty_pages = 0
    for _ in range(AMAZON_MAX_PAGES):
        # Per-page global Amazon spacing
        now = time.time()
        since_last = now - _last_amazon_fetch_ts
        if since_last < AMAZON_MIN_SPACING:
            wait_for = AMAZON_MIN_SPACING - since_last
            logger.info(
                "Amazon fetch spacing: last request %.1fs ago; waiting %.1fs before fetching '%s'.",
                since_last,
                wait_for,
                wishlist_name or identifier,
            )
            time.sleep(wait_for)
        _last_amazon_fetch_ts = time.time()

        if next_url == current_url:
            logger.warning("Pagination loop detected: next page is same as current. Stopping.")
            break

        current_url = next_url
  # guard against infinite loops
        if not next_url:
            break
        logger.debug("Fetching Amazon page: %s", next_url)
        html = fetch_page(next_url)

        items = extract_items_from_html(html)
        logger.debug("Extracted %d items from page.", len(items))
        if len(items) == 0:
            empty_pages += 1
            if empty_pages >= 2:
                logger.info("Two consecutive empty pages encountered. Ending pagination.")
                break
        else:
            empty_pages = 0
        all_items.extend(items)

        next_url = extract_next_page(html)

    logger.info(
        "Extracted %d total Amazon items for wishlist '%s'.",
        len(all_items),
        wishlist_name or identifier,
    )

    return all_items
