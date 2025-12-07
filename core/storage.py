# core/storage.py
import os
import sqlite3
import datetime
import pytz
from typing import Dict, List, Tuple

from .models import Item
from .logger import get_logger

logger = get_logger(__name__)

DB_PATH = os.getenv("DB_PATH", "/data/wishlist_state.sqlite3")


def _connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return sqlite3.connect(DB_PATH)


def now_utc_iso() -> str:
    return datetime.datetime.now(tz=pytz.UTC).isoformat()


def ensure_db():
    with _connect() as con:
        cur = con.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS items (
                platform TEXT,
                wishlist_id TEXT,
                item_id TEXT,
                name TEXT,
                price_cents INTEGER,
                currency TEXT,
                product_url TEXT,
                image_url TEXT,
                available INTEGER,
                first_seen TEXT,
                last_seen TEXT,
                PRIMARY KEY (platform, wishlist_id, item_id)
            )
        """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT,
                platform TEXT,
                wishlist_id TEXT,
                event_type TEXT,   -- added|removed|price_change
                item_id TEXT,
                name TEXT,
                from_price_cents INTEGER,
                to_price_cents INTEGER
            )
        """
        )
        con.commit()


def get_previous_items(platform: str, wishlist_id: str) -> Dict[str, Item]:
    """
    Return mapping item_id -> Item for existing DB entries.
    """
    with _connect() as con:
        cur = con.cursor()
        cur.execute(
            """
            SELECT item_id, name, price_cents, currency, product_url, image_url, available
            FROM items
            WHERE platform=? AND wishlist_id=?
        """,
            (platform, wishlist_id),
        )
        rows = cur.fetchall()

    out: Dict[str, Item] = {}
    for row in rows:
        item_id, name, price_cents, currency, product_url, image_url, available = row
        out[item_id] = Item(
            item_id=item_id,
            name=name,
            price_cents=price_cents,
            currency=currency,
            product_url=product_url or "",
            image_url=image_url or "",
            available=bool(available),
        )
    return out


def get_previous_item_count(platform: str, wishlist_id: str) -> int:
    with _connect() as con:
        cur = con.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM items WHERE platform=? AND wishlist_id=?",
            (platform, wishlist_id),
        )
        row = cur.fetchone()
    return row[0] if row and row[0] is not None else 0


def save_items_and_events(
    platform: str,
    wishlist_id: str,
    new_items: List[Item],
    added: List[Item],
    removed: List[Item],
    price_changes: List[Tuple[Item, int, int]],
):
    """
    Persist current items and diff events into SQLite.
    """
    ts = now_utc_iso()
    with _connect() as con:
        cur = con.cursor()

        # Upsert all current items
        for it in new_items:
            cur.execute(
                """
                INSERT INTO items (
                    platform, wishlist_id, item_id, name, price_cents, currency,
                    product_url, image_url, available, first_seen, last_seen
                )
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(platform, wishlist_id, item_id) DO UPDATE SET
                    name=excluded.name,
                    price_cents=excluded.price_cents,
                    currency=excluded.currency,
                    product_url=excluded.product_url,
                    image_url=excluded.image_url,
                    available=excluded.available,
                    last_seen=excluded.last_seen
            """,
                (
                    platform,
                    wishlist_id,
                    it.item_id,
                    it.name,
                    it.price_cents,
                    it.currency,
                    it.product_url,
                    it.image_url,
                    1 if it.available else 0,
                    ts,
                    ts,
                ),
            )

        # Events for added
        for it in added:
            cur.execute(
                """
                INSERT INTO events (
                    ts, platform, wishlist_id, event_type,
                    item_id, name, from_price_cents, to_price_cents
                )
                VALUES (?,?,?,?,?,?,?,?)
            """,
                (
                    ts,
                    platform,
                    wishlist_id,
                    "added",
                    it.item_id,
                    it.name,
                    None,
                    it.price_cents,
                ),
            )

        # Events for price changes
        for it, before, after in price_changes:
            cur.execute(
                """
                INSERT INTO events (
                    ts, platform, wishlist_id, event_type,
                    item_id, name, from_price_cents, to_price_cents
                )
                VALUES (?,?,?,?,?,?,?,?)
            """,
                (
                    ts,
                    platform,
                    wishlist_id,
                    "price_change",
                    it.item_id,
                    it.name,
                    before,
                    after,
                ),
            )

        # Events + deletion for removed
        for it in removed:
            cur.execute(
                """
                INSERT INTO events (
                    ts, platform, wishlist_id, event_type,
                    item_id, name, from_price_cents, to_price_cents
                )
                VALUES (?,?,?,?,?,?,?,?)
            """,
                (
                    ts,
                    platform,
                    wishlist_id,
                    "removed",
                    it.item_id,
                    it.name,
                    None,
                    None,
                ),
            )
            cur.execute(
                "DELETE FROM items WHERE platform=? AND wishlist_id=? AND item_id=?",
                (platform, wishlist_id, it.item_id),
            )

        con.commit()
