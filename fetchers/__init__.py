# fetchers/__init__.py
from . import amazon
from . import throne

FETCHERS = {
    "amazon": amazon.fetch_items,
    "throne": throne.fetch_items,
}
