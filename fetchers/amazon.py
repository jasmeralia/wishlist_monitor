# fetchers/amazon.py
import os
import re
import time
import random
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

from core.models import Item
from core.logger import get_logger

logger = get_logger(__name__)

# Mobile wishlist URL template
MOBILE_LIST_URL = "https://www.amazon.com/gp/aw/ls?lid={}&ty=wishlist"

# ENV settings (compatible with your previous script)
PAGE_SLEEP = int(os.getenv("PAGE_SLEEP", "5"))
FAIL_SLEEP = int(os.getenv("FAIL_SLEEP", "600"))
RETRY_COUNT = int(os.getenv("RETRY_COUNT", "3"))
RETRY_SLEEP = int(os.getenv("RETRY_SLEEP", "60"))
CAPTCHA_SLEEP = int(os.getenv("CAPTCHA_SLEEP", "600"))
DEBUG_HTML = os.getenv("AMAZON_DEBUG_HTML", "0") == "1"
DEBUG_HTML_DIR = os.getenv("AMAZON_DEBUG_DIR", "/data/amazon_debug")

TOP_MOBILE_USER_AGENTS = [
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Mobile Safari/537.3",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_3_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3.1 Mobile/15E148 Safari/604.",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_3_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) GSA/360.1.737798518 Mobile/15E148 Safari/604.",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_3_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/134.0.6998.99 Mobile/15E148 Safari/604.",
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/27.0 Chrome/125.0.0.0 Mobile Safari/537.3",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_3_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3 Mobile/15E148 Safari/604.",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1.1 Mobile/15E148 Safari/604.",
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.3",
    "Mozilla/5.0 (Android 14; Mobile; rv:136.0) Gecko/136.0 Firefox/136.0",
]


def _get_random_user_agent() -> str:
    return random.choice(TOP_MOBILE_USER_AGENTS)


def _normalize_wishlist_url(url_or_id: str) -> str:
    """
    Normalize Amazon wishlist URLs / IDs to the mobile wishlist URL format.
    If given just an ID, treat it as such.
    """
    s = url_or_id.strip()
    if re.fullmatch(r"[A-Za-z0-9]+", s):
        return MOBILE_LIST_URL.format(s)

    m = re.search(r"/hz/wishlist/ls/([A-Za-z0-9]+)/?", s)
    if not m:
        m = re.search(r"/gp/registry/(?:wishlist|list)/([A-Za-z0-9]+)/?", s)
    if m:
        return MOBILE_LIST_URL.format(m.group(1))
    return s


def _format_price_to_cents(price_str: Optional[str]) -> int:
    if not price_str:
        return -1
    try:
        p = float(
            str(price_str)
            .replace("$", "")
            .replace(",", "")
            .replace("US$", "")
            .strip()
        )
        return int(round(p * 100))
    except Exception:
        return -1


def _maybe_debug_html(wishlist_name: str, suffix: str, html: str):
    if not DEBUG_HTML:
        return
    try:
        os.makedirs(DEBUG_HTML_DIR, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9]+", "_", wishlist_name) or "wishlist"
        fname = os.path.join(
            DEBUG_HTML_DIR, f"{safe_name}_{suffix}.html"
        )
        with open(fname, "w", encoding="utf-8") as f:
            f.write(html)
        logger.info("Amazon debug HTML written: %s", fname)
    except Exception as e:
        logger.warning("Failed to write Amazon debug HTML: %s", e)


def fetch_items(identifier: str, wishlist_name: str | None = None) -> Optional[List[Item]]:
    """
    Fetch all items from an Amazon wishlist (mobile view), returning a list of Items
    or None on failure.
    """
    session = requests.Session()
    url = _normalize_wishlist_url(identifier)
    ua = _get_random_user_agent()
    headers = {
        "User-Agent": ua,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.amazon.com/",
    }

    logger.info("Checking Amazon wishlist '%s' at %s", wishlist_name or identifier, url)

    items: List[Item] = []
    seen_keys: set[str] = set()
    page = 1
    next_url = url

    try:
        while next_url:
            captcha_attempts = 0
            while True:
                for attempt in range(1, RETRY_COUNT + 1):
                    try:
                        resp = session.get(
                            next_url, headers=headers, timeout=20
                        )
                        if resp.status_code == 200:
                            break
                        logger.warning(
                            "Amazon page %d HTTP %d (attempt %d/%d)",
                            page,
                            resp.status_code,
                            attempt,
                            RETRY_COUNT,
                        )
                    except Exception as e:
                        logger.warning(
                            "Amazon exception on page %d attempt %d/%d: %s",
                            page,
                            attempt,
                            RETRY_COUNT,
                            e,
                        )
                    if attempt < RETRY_COUNT:
                        sd = random.uniform(
                            RETRY_SLEEP * 0.5, RETRY_SLEEP * 1.5
                        )
                        logger.info(
                            "Sleeping %.1fs before retrying Amazon page %d",
                            sd,
                            page,
                        )
                        time.sleep(sd)
                    else:
                        sd = random.uniform(
                            FAIL_SLEEP * 0.5, FAIL_SLEEP * 1.5
                        )
                        logger.error(
                            "Failed to fetch Amazon page %d after retries; sleeping %.1fs and aborting wishlist.",
                            page,
                            sd,
                        )
                        time.sleep(sd)
                        return None

                text_lower = resp.text.lower()
                captcha_detected = (
                    "captcha" in text_lower
                    or "enter the characters you see" in text_lower
                )
                if captcha_detected:
                    captcha_attempts += 1
                    sd = random.uniform(
                        CAPTCHA_SLEEP * 0.5, CAPTCHA_SLEEP * 1.5
                    )
                    logger.warning(
                        "Amazon CAPTCHA detected on page %d (attempt %d/%d); sleeping %.1fs.",
                        page,
                        captcha_attempts,
                        RETRY_COUNT,
                        sd,
                    )
                    time.sleep(sd)
                    if captcha_attempts < RETRY_COUNT:
                        continue
                    else:
                        logger.error(
                            "Max CAPTCHA retries reached for Amazon wishlist; aborting."
                        )
                        return None
                break  # no captcha, proceed

            soup = BeautifulSoup(resp.text, "html.parser")
            li_items = soup.select("li[id^='itemWrapper_']")

            if not li_items:
                logger.warning(
                    "Amazon: no items found on page %d for %s",
                    page,
                    wishlist_name or identifier,
                )
                _maybe_debug_html(
                    wishlist_name or "wishlist",
                    f"page{page}_no_items",
                    resp.text,
                )
                break

            page_count = 0
            for li in li_items:
                link = li.select_one("a[href^='/dp']")
                if not link:
                    continue
                
                href_raw = link.get("href")
                if not isinstance(href_raw, str):
                    continue
                href = href_raw.split("?")[0]
                if not href.startswith("http"):
                    href = "https://www.amazon.com" + href
                
                title_el = li.select_one(".awl-item-title")
                name = title_el.get_text(strip=True) if title_el else None
                
                price_elem = li.select_one("span.a-price-whole")
                price_raw = price_elem.get_text(strip=True) if price_elem else None
                if isinstance(price_raw, str):
                    price_cents = _format_price_to_cents(price_raw)
                else:
                    price_cents = -1

                key = href or name or ""
                if not key:
                    continue

                if key in seen_keys:
                    continue
                seen_keys.add(key)

                items.append(
                    Item(
                        item_id=key,
                        name=name or "(no name)",
                        price_cents=price_cents,
                        currency="USD",
                        product_url=href,
                        image_url="",
                        available=True,
                    )
                )
                page_count += 1

            logger.info(
                "Amazon page %d: found %d new items (total %d)",
                page,
                page_count,
                len(items),
            )

            token_input = soup.select_one(
                "form.scroll-state input.showMoreUrl"
            )
            token_value = token_input.get("value") if token_input else None
            if isinstance(token_value, str) and token_value:
                next_url = "https://www.amazon.com" + token_value
                page += 1
                sd = random.uniform(
                    PAGE_SLEEP * 0.5, PAGE_SLEEP * 1.5
                )
                logger.info(
                    "Sleeping %.1fs before Amazon page %d", sd, page
                )
                time.sleep(sd)
            else:
                logger.info("Amazon pagination complete.")
                break

        return items

    except Exception:
        sd = random.uniform(FAIL_SLEEP * 0.5, FAIL_SLEEP * 1.5)
        logger.exception(
            "Amazon fetch exception for %s; sleeping %.1fs and aborting.",
            identifier,
            sd,
        )
        time.sleep(sd)
        return None
