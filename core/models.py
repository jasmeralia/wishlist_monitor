# core/models.py
from dataclasses import dataclass


@dataclass
class Item:
    """
    Normalized representation of a wishlist item across all platforms.
    Prices are stored in cents for consistency.
    """
    item_id: str
    name: str
    price_cents: int = -1
    currency: str = "USD"
    product_url: str = ""
    image_url: str = ""
    available: bool = True
