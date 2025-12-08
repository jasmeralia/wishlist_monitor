import os
import time
import requests
from urllib.parse import urlparse
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


class AmazonError(Exception):
    pass


def ensure_absolute_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc:
        return url
    return f"{BASE_URL}{url}"


def parse_item_div(div) -> Item | None:
    data = div.get("data-itemid")
    if not data:
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


def fetch_page_raw(url: str) -> str:
    logger.debug("Fetching Amazon page: %s", url)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15A372 Safari/604.1"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    resp = requests.get(url, headers=headers, timeout=30)

    status = resp.status_code
    text = resp.text

    if status == 503:
        raise AmazonError("503 Service Unavailable")

    if status != 200:
        raise AmazonError(f"Bad status code {status}")

    time.sleep(PAGE_SLEEP)
    return text


def looks_like_captcha_or_block(html: str) -> bool:
    lower = html.lower()
    return any(
        key in lower
        for key in [
            "robot check",
            "enter the characters you see below",
            "/errors/validatecaptcha",
            "to discuss automated access to amazon data",
        ]
    )


def extract_items_from_html(html: str) -> list[Item]:
    soup = BeautifulSoup(html, "html.parser")
    divs = soup.select("div.g-item-sortable")
    items: list[Item] = []
    for div in divs:
        it = parse_item_div(div)
        if it:
            items.append(it)
    return items


def extract_next_page(html: str, current_url: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    link = soup.select_one("li.a-last a")
    if not link or not link.has_attr("href"):
        return None

    href_raw = str(link["href"])
    href = ensure_absolute_url(href_raw)

    if href == current_url:
        return None

    return href


def _apply_global_spacing(wishlist_name: str | None, identifier: str) -> None:
    global _last_amazon_fetch_ts
    now = time.time()
    since_last = now - _last_amazon_fetch_ts
    if since_last < AMAZON_MIN_SPACING:
        time.sleep(AMAZON_MIN_SPACING - since_last)
    _last_amazon_fetch_ts = time.time()


def fetch_items(identifier: str, wishlist_name: str | None = None) -> list[Item]:
    if identifier.startswith("http://") or identifier.startswith("https://"):
        url = identifier
    else:
        url = f"{BASE_URL}/hz/wishlist/ls/{identifier}"

    all_items: list[Item] = []
    next_url: str | None = url
    current_url: str | None = None
    empty_pages = 0

    for _ in range(AMAZON_MAX_PAGES):
        if not next_url:
            break

        if next_url == current_url:
            break

        current_url = next_url
        _apply_global_spacing(wishlist_name, identifier)

        attempt = 0
        while True:
            try:
                html = fetch_page_raw(current_url)
                if looks_like_captcha_or_block(html):
                    time.sleep(CAPTCHA_SLEEP)
                    continue
                break
            except AmazonError:
                attempt += 1
                if attempt >= MAX_PAGE_RETRIES:
                    return all_items
                time.sleep(FAIL_SLEEP)
                continue

        items = extract_items_from_html(html)

        if len(items) == 0:
            empty_pages += 1
            if empty_pages >= 2:
                break
        else:
            empty_pages = 0

        all_items.extend(items)

        raw_next = extract_next_page(html, current_url)
        if not raw_next:
            break
        next_url = raw_next

    return all_items
