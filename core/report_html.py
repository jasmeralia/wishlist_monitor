import os
from pathlib import Path
from typing import List, Tuple
from jinja2 import Environment, FileSystemLoader
from core.models import Item

# Resolve template directory relative to this file
TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))

EMAIL_THEME = os.getenv("EMAIL_THEME", "dark").strip().lower()
if EMAIL_THEME not in ("light", "dark"):
    EMAIL_THEME = "dark"

THEMES = {
    "light": {
        "page_bg": "#f5f5f5",
        "card_bg": "#ffffff",
        "card_border": "#e0e0e0",
        "text_primary": "#202124",
        "text_secondary": "#555",
        "text_muted": "#999",
        "price_increase": "#c62828",
        "price_decrease": "#2e7d32",
        "link_color": "#1a73e8",
    },
    "dark": {
        "page_bg": "#121212",
        "card_bg": "#1E1E1E",
        "card_border": "#333333",
        "text_primary": "#F1F1F1",
        "text_secondary": "#BBBBBB",
        "text_muted": "#777777",
        "price_increase": "#FF6B6B",
        "price_decrease": "#4CAF50",
        "link_color": "#8AB4F8",
    },
}

def _cents_to_str(cents: int | None, currency: str = "USD") -> str:
    if cents is None or cents < 0:
        return "Unavailable"
    sym = "$" if currency == "USD" else ""
    return f"{sym}{cents/100:.2f}"

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
    template = env.get_template("email_text.txt")

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

    summary_text = f"{len(added)} added · {len(removed)} removed · {len(price_changes)} price changes\nPrevious: {previous_count} · Current: {new_count}"

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
    template = env.get_template(f"email_{EMAIL_THEME}.html")
    colors = THEMES[EMAIL_THEME]

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
        color = colors["text_secondary"]
        if before and before > 0 and after and after > 0:
            delta = after - before
            pct = abs(delta) * 100.0 / abs(before)
            sign = "+" if delta > 0 else "-"
            pct_str = f"({sign}{pct:.1f}%)"
            color = colors["price_increase"] if delta > 0 else colors["price_decrease"]
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
        f"<div style='color:{colors['text_secondary']}'>"
        f"<strong>Summary:</strong> {len(added)} added · {len(removed)} removed · "
        f"{len(price_changes)} price changes<br>"
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
        "colors": colors,
    }

    return template.render(**ctx)
