# core/diff.py
import os
from typing import Dict, List, Tuple

from .models import Item

PRICE_NOTIFY_THRESHOLD = float(os.getenv("PRICE_NOTIFY_THRESHOLD", "20"))


def diff_items(
    previous: Dict[str, Item], current: List[Item]
) -> tuple[List[Item], List[Item], List[Tuple[Item, int, int]]]:
    """
    Compute added, removed, and price_changes between previous and current.
    - previous: mapping item_id -> Item
    - current: list of Items
    Returns:
      (added_items, removed_items, price_changes[(item_after, before_cents, after_cents)])
    """
    new_map = {it.item_id: it for it in current}
    old_ids = set(previous.keys())
    new_ids = set(new_map.keys())

    added = [new_map[iid] for iid in new_ids - old_ids]
    removed = [previous[iid] for iid in old_ids - new_ids]

    price_changes: List[Tuple[Item, int, int]] = []

    for iid in old_ids & new_ids:
        old_item = previous[iid]
        new_item = new_map[iid]
        before = old_item.price_cents
        after = new_item.price_cents

        if before == after:
            continue

        # If either price is unknown (<0), always include
        if before is None or after is None or before < 0 or after < 0:
            price_changes.append((new_item, before, after))
            continue

        # Threshold logic (like your Amazon monitor)
        if before == 0:
            pct = 100.0
        else:
            pct = abs(after - before) * 100.0 / abs(before)

        if pct >= PRICE_NOTIFY_THRESHOLD:
            price_changes.append((new_item, before, after))

    return added, removed, price_changes
