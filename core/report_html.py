# core/report_html.py
from typing import List, Tuple

from .models import Item


def _cents_to_str(cents: int | None, currency: str = "USD") -> str:
    if cents is None or cents < 0:
        return "Unavailable"
    sym = (
        "$"
        if currency == "USD"
        else ("€" if currency == "EUR" else ("£" if currency == "GBP" else ""))
    )
    return f"{sym}{cents/100:.2f}" if sym else f"{cents/100:.2f} {currency}"


def _price_delta(
    before: int | None, after: int | None
) -> tuple[str | None, float | None]:
    """
    Return (sign_str, pct) where sign_str is '+' or '-' and pct is magnitude.
    Returns (None, None) if not computable (unavailable or zero baseline).
    """
    if (
        before is None
        or after is None
        or before < 0
        or after < 0
        or before == 0
    ):
        return None, None

    delta = after - before
    if delta == 0:
        return None, None

    sign = "+" if delta > 0 else "-"
    pct = abs(delta) * 100.0 / abs(before)
    return sign, pct


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
    unchanged = max(previous_count - len(removed) - len(price_changes), 0)

    lines: List[str] = []
    lines.append(f"Platform: {platform.capitalize()}")
    lines.append(f"Wishlist: {wishlist_name} ({wishlist_id})")
    lines.append(
        f"Summary: {len(added)} added, {len(removed)} removed, "
        f"{len(price_changes)} price changes, {unchanged} unchanged"
    )
    lines.append("")
    lines.append(f"Previous items: {previous_count}, current items: {new_count}")
    lines.append("")

    if added:
        lines.append("Added:")
        for it in added:
            lines.append(
                f"  • {it.name} | {_cents_to_str(it.price_cents, it.currency)} | {it.product_url or 'URL not found'}"
            )
        lines.append("")

    if removed:
        lines.append("Removed:")
        for it in removed:
            lines.append(f"  • {it.name}")
        lines.append("")

    if price_changes:
        lines.append("Price changes:")
        for it, before, after in price_changes:
            before_str = _cents_to_str(before, it.currency)
            after_str = _cents_to_str(after, it.currency)
            sign, pct = _price_delta(before, after)
            pct_str = ""
            if pct is not None and sign is not None:
                pct_str = f" ({sign}{pct:.1f}%)"
            lines.append(
                f"  • {it.name}: {before_str} -> {after_str}{pct_str} "
                f"| {it.product_url or 'URL not found'}"
            )

    return "\n".join(lines)


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
    unchanged = max(previous_count - len(removed) - len(price_changes), 0)

    def render_item_card(it: Item, extra_html: str = "") -> str:
        price_str = _cents_to_str(it.price_cents, it.currency)
        url = it.product_url or ""
        img_html = (
            f'<img src="{it.image_url}" alt="" '
            'style="width:64px;height:64px;object-fit:cover;border-radius:6px;margin-right:12px;" />'
            if it.image_url
            else ""
        )
        link_html = (
            f'<a href="{url}" style="color:#1a73e8;text-decoration:none;">View item</a>'
            if url
            else ""
        )
        return f"""
        <div style="display:flex;align-items:center;border:1px solid #eee;
                    border-radius:8px;padding:8px 10px;margin-bottom:8px;background:#fafafa;">
            {img_html}
            <div style="flex:1;min-width:0;">
                <div style="font-weight:600;font-size:14px;margin-bottom:2px;">
                    {it.name}
                </div>
                <div style="font-size:12px;color:#555;margin-bottom:2px;">
                    {price_str}{extra_html}
                </div>
                <div style="font-size:12px;">
                    {link_html}
                </div>
            </div>
        </div>
        """

    def render_price_change_card(
        it: Item, before: int | None, after: int | None
    ) -> str:
        before_str = _cents_to_str(before, it.currency)
        after_str = _cents_to_str(after, it.currency)

        # Case 1: price became unavailable
        if after is not None and after < 0 and (before is None or before >= 0):
            extra_html = (
                f'<span style="margin-left:6px;font-weight:600;color:#c62828;">'
                f"{before_str} → Unavailable</span>"
            )
            return render_item_card(it, extra_html=extra_html)

        # Case 2: price was unavailable and became available
        if before is not None and before < 0 and (after is None or after >= 0):
            extra_html = (
                f'<span style="margin-left:6px;font-weight:600;color:#555;">'
                f"Unavailable → {after_str}</span>"
            )
            return render_item_card(it, extra_html=extra_html)

        # Case 3: both unavailable (weird, but handle gracefully)
        if (before is None or before < 0) and (after is None or after < 0):
            extra_html = (
                '<span style="margin-left:6px;font-weight:600;color:#c62828;">'
                "Price unavailable</span>"
            )
            return render_item_card(it, extra_html=extra_html)

        # Normal price change with percentage
        sign, pct = _price_delta(before, after)
        pct_html = ""
        if pct is not None and sign is not None:
            color = "#c62828" if sign == "+" else "#2e7d32"
            pct_html = (
                f'<span style="margin-left:6px;font-weight:600;color:{color};">'
                f"({sign}{pct:.1f}%)</span>"
            )

        extra_html = (
            f'<span style="margin-left:6px;color:#555;">'
            f"{before_str} → {after_str}</span>{pct_html}"
        )
        return render_item_card(it, extra_html=extra_html)

    def render_section(title: str, color: str, body: str) -> str:
        return f"""
        <div style="margin-top:18px;">
            <div style="font-size:15px;font-weight:600;color:{color};margin-bottom:6px;">
                {title}
            </div>
            {body}
        </div>
        """

    added_html = ""
    if added:
        cards = [render_item_card(it) for it in added]
        added_html = render_section("🟢 Added", "#2e7d32", "".join(cards))

    removed_html = ""
    if removed:
        cards = [
            render_item_card(
                Item(
                    item_id=it.item_id,
                    name=it.name,
                    price_cents=-1,
                    currency=it.currency,
                    product_url="",
                    image_url="",
                    available=False,
                ),
                extra_html=(
                    '<span style="margin-left:6px;color:#c62828;">Removed</span>'
                ),
            )
            for it in removed
        ]
        removed_html = render_section("🔴 Removed", "#c62828", "".join(cards))

    price_html = ""
    if price_changes:
        cards = []
        for it, before, after in price_changes:
            cards.append(render_price_change_card(it, before, after))
        price_html = render_section(
            "🟡 Price changes", "#f9a825", "".join(cards)
        )

    platform_title = platform.capitalize()

    html = f"""
<!DOCTYPE html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>Wishlist update – {platform_title}</title>
  </head>
  <body style="margin:0;padding:0;background:#f5f5f5;">
    <div style="max-width:800px;margin:0 auto;padding:16px;">
      <div style="background:#ffffff;border-radius:10px;border:1px solid #e0e0e0;
                  padding:16px 18px;font-family:system-ui,-apple-system,BlinkMacSystemFont,
                  'Segoe UI',sans-serif;font-size:14px;color:#202124;">
        <div style="margin-bottom:10px;">
          <div style="font-size:18px;font-weight:600;margin-bottom:4px;">
            Wishlist update – {platform_title}
          </div>
          <div style="font-size:13px;color:#555;">
            <strong>Wishlist:</strong> {wishlist_name} <span style="color:#999;">({wishlist_id})</span>
          </div>
        </div>

        <div style="font-size:13px;color:#555;margin-bottom:10px;">
          <strong>Summary:</strong>
          {len(added)} added · {len(removed)} removed · {len(price_changes)} price changes · {unchanged} unchanged
          <br/>
          <span style="color:#999;">Previous items: {previous_count} · Current items: {new_count}</span>
        </div>

        {added_html}
        {removed_html}
        {price_html}

        <div style="margin-top:20px;font-size:11px;color:#999;">
          This email was generated automatically by your wishlist monitor.
        </div>
      </div>
    </div>
  </body>
</html>
"""
    return html
