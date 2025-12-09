import os
from pathlib import Path
from typing import List, Tuple

from jinja2 import Environment, FileSystemLoader

from core.models import Item

# Resolve template directory relative to this file:
TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))

EMAIL_THEME = os.getenv("EMAIL_THEME", "dark").strip().lower()
if EMAIL_THEME not in ("light", "dark"):
    EMAIL_THEME = "dark"


def _cents_to_str(cents: int | None, currency: str = "USD") -> str:
    if cents is None or cents < 0:
        return "Unavailable"
    sym = "$" if currency == "USD" else ""
    return f"{sym}{cents / 100:.2f}"


def build_plaintext_report(
    platform: str,
    wishlist_name: str,
    wishlist_id: str,
    added: List[Item],
    removed: List[Item],
    price_changes: List[Tuple[Item, int, int]],
    previous_count: int,
    new_count: int,
) -> str:
    """Render the plaintext version using templates/email_text.txt."""
    template = env.get_template("email_text.txt")

    summary_text = (
        f"{len(added)} added · {len(removed)} removed · "
        f"{len(price_changes)} price changes\n"
        f"Previous: {previous_count} · Current: {new_count}"
    )

    added_data = [
        {
            "name": it.name,
            "price_str": _cents_to_str(it.price_cents, it.currency),
            "product_url": it.product_url,
        }
        for it in added
    ]

    removed_data = [{"name": it.name} for it in removed]

    price_change_data = []
    for it, before, after in price_changes:
        before_str = _cents_to_str(before, it.currency)
        after_str = _cents_to_str(after, it.currency)
        pct_str = ""
        if before and before > 0 and after and after > 0:
            delta = after - before
            pct = abs(delta) * 100.0 / abs(before)
            sign = "+" if delta > 0 else "-"
            pct_str = f"({sign}{pct:.1f}%)"
        price_change_data.append(
            {
                "item": {"name": it.name},
                "before_str": before_str,
                "after_str": after_str,
                "pct_str": pct_str,
            }
        )

    ctx = {
        "platform": platform,
        "wishlist_name": wishlist_name,
        "wishlist_id": wishlist_id,
        "summary_text": summary_text,
        "added": added_data,
        "removed": removed_data,
        "price_changes": price_change_data,
    }

    return template.render(**ctx)


def build_html_report(
    platform: str,
    wishlist_name: str,
    wishlist_id: str,
    added: List[Item],
    removed: List[Item],
    price_changes: List[Tuple[Item, int, int]],
    previous_count: int,
    new_count: int,
) -> str:
    """Render the HTML version using email_dark.html / email_light.html."""
    template_name = f"email_{EMAIL_THEME}.html"
    template = env.get_template(template_name)

    added_data = [
        {
            "name": it.name,
            "price_str": _cents_to_str(it.price_cents, it.currency),
            "image_url": it.image_url,
            "product_url": it.product_url,
        }
        for it in added
    ]

    removed_data = [{"name": it.name} for it in removed]

    price_change_data = []
    for it, before, after in price_changes:
        before_str = _cents_to_str(before, it.currency)
        after_str = _cents_to_str(after, it.currency)
        pct_str = ""
        color = "#BBBBBB"
        if before and before > 0 and after and after > 0:
            delta = after - before
            pct = abs(delta) * 100.0 / abs(before)
            sign = "+" if delta > 0 else "-"
            pct_str = f"({sign}{pct:.1f}%)"
            color = "#FF6B6B" if delta > 0 else "#4CAF50"
        price_change_data.append(
            {
                "item": {
                    "name": it.name,
                    "image_url": it.image_url,
                    "product_url": it.product_url,
                },
                "before_str": before_str,
                "after_str": after_str,
                "pct_str": pct_str,
                "color": color,
            }
        )

    summary_html = (
        f"<div><strong>Summary:</strong> {len(added)} added · "
        f"{len(removed)} removed · {len(price_changes)} price changes<br>"
        f"Previous: {previous_count} · Current: {new_count}</div>"
    )

    ctx = {
        "title": f"Wishlist update – {platform}",
        "platform": platform,
        "wishlist_name": wishlist_name,
        "wishlist_id": wishlist_id,
        "summary_html": summary_html,
        "added": added_data,
        "removed": removed_data,
        "price_changes": price_change_data,
    }

    return template.render(**ctx)
