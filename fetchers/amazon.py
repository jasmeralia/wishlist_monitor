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
_last_fetch: float = 0.0
AMAZON_MAX_PAGES = int(os.getenv("AMAZON_MAX_PAGES", "50"))
MAX_RETRIES = int(os.getenv("AMAZON_MAX_PAGE_RETRIES", "3"))
PAGE_SLEEP = int(os.getenv("PAGE_SLEEP", "5"))
CAPTCHA_SLEEP = int(os.getenv("CAPTCHA_SLEEP", "900"))
FAIL_SLEEP = int(os.getenv("FAIL_SLEEP", "30"))

DUMP_DIR = Path("/data/debug_dumps")
DUMP_DIR.mkdir(parents=True, exist_ok=True)


class AmazonError(Exception):
    pass


def _sanitize(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)


def _dump_html(wishlist_name: str | None, page_index: int, html: str) -> None:
    if not logger.isEnabledFor(logging.DEBUG):
        return
    ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    wl = _sanitize(wishlist_name or "unknown")
    path = DUMP_DIR / f"amazon_{wl}_page{page_index}_{ts}.html"
    try:
        path.write_text(html, encoding="utf-8")
        logger.debug("Dumped HTML to %s", path)
    except Exception as exc:
        logger.debug("Failed dump to %s: %s", path, exc)


def ensure_absolute_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc:
        return url
    return f"{BASE_URL}{url}"


def parse_item_li(li) -> Item | None:
    item_id = li.get("id") or ""

    img_el = li.select_one("div.awl-item-image img")
    image_url = img_el["src"] if img_el and img_el.has_attr("src") else ""

    link_el = li.select_one("a[href*='/dp/']")
    href = link_el["href"] if link_el and link_el.has_attr("href") else ""
    product_url = ensure_absolute_url(href) if href else ""

    title_el = li.select_one("h3.awl-item-title")
    name = title_el.get_text(strip=True) if title_el else item_id

    price_cents = -1
    pw = li.select_one(".a-price-whole")
    pf = li.select_one(".a-price-fraction")
    if pw:
        try:
            whole = pw.get_text(strip=True).replace(",", "")
            fraction = pf.get_text(strip=True) if pf else "00"
            price_value = float(f"{whole}.{fraction}")
            price_cents = int(round(price_value * 100))
        except Exception:
            pass

    logger.debug("Parsed item: %s | %s | %s", name, product_url, image_url)

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
    logger.debug("Fetching %s", url)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko)"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }
    resp = requests.get(url, headers=headers, timeout=30)
    if resp.status_code != 200:
        raise AmazonError(f"Bad status {resp.status_code}")
    time.sleep(PAGE_SLEEP)
    return resp.text


def looks_like_captcha(html: str) -> bool:
    lower_html = html.lower()
    return any(
        marker in lower_html
        for marker in [
            "robot check",
            "enter the characters",
            "validatecaptcha",
            "automated access",
        ]
    )


def extract_items(html: str) -> list[Item]:
    soup = BeautifulSoup(html, "html.parser")
    li_items = soup.select("li.awl-item-wrapper")
    logger.debug("Found %d li.awl-item-wrapper elements", len(li_items))

    items: list[Item] = []
    for li in li_items:
        item = parse_item_li(li)
        if item:
            items.append(item)
    return items


def extract_next_page(html: str, current_url: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    link = soup.select_one("li.a-last a")
    if not link or not link.has_attr("href"):
        return None

    href = ensure_absolute_url(str(link["href"]))
    if href == current_url:
        return None
    return href


def _apply_spacing(wishlist_name: str | None) -> None:
    global _last_fetch
    now = time.time()
    elapsed = now - _last_fetch
    if elapsed < AMAZON_MIN_SPACING:
        wait_time = AMAZON_MIN_SPACING - elapsed
        logger.debug("Spacing wait %.1f seconds", wait_time)
        time.sleep(wait_time)
    _last_fetch = time.time()


def fetch_items(identifier: str, wishlist_name: str | None = None) -> list[Item]:
    if identifier.startswith("http://") or identifier.startswith("https://"):
        url = identifier
    else:
        url = f"{BASE_URL}/hz/wishlist/ls/{identifier}"

    logger.info("Fetching Amazon wishlist '%s' at %s", wishlist_name, url)

    all_items: list[Item] = []
    next_url: str | None = url
    current_url: str | None = None
    empty_pages = 0

    for page_index in range(AMAZON_MAX_PAGES):
        if not next_url or next_url == current_url:
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
                        "CAPTCHA detected for wishlist '%s'; sleeping %s seconds",
                        wishlist_name,
                        CAPTCHA_SLEEP,
                    )
                    time.sleep(CAPTCHA_SLEEP)
                    continue
                break
            except AmazonError:
                attempt += 1
                if attempt >= MAX_RETRIES:
                    return all_items
                time.sleep(FAIL_SLEEP)

        page_items = extract_items(html)
        if not page_items:
            empty_pages += 1
            if empty_pages >= 2:
                break
        else:
            empty_pages = 0

        all_items.extend(page_items)
        next_url = extract_next_page(html, current_url)

    logger.info("Extracted %d items.", len(all_items))
    return all_items
