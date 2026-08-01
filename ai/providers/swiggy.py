"""
Swiggy Instamart provider — GROCERY only.

This is the one file permitted to know Swiggy exists. It translates Instamart's
product payloads into neutral `Offer`s and nothing above it can tell which
platform answered.

SCOPE: Instamart is a *grocery* catalogue. It has no restaurants, menus, ratings
or delivery ETAs, so this provider registers under ProviderKind.GROCERY and
leaves `rating`/`eta_minutes` as None rather than fabricating them. Restaurant
search needs a different provider — see MIGRATION.md §0.
"""
import asyncio

from core.logger import logger

from ai.providers.base import Offer, ProviderKind, SearchContext
from integrations.swiggy.swiggy_mcp import SwiggyInstamart


def _to_float(value):
    """Prices arrive as numbers, strings, or paise. Unparseable -> None (unknown)
    rather than 0, which would read as 'free'."""
    if value is None:
        return None
    try:
        number = float(str(value).replace("₹", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _first(product: dict, *keys):
    for key in keys:
        value = product.get(key)
        if value not in (None, "", []):
            return value
    return None


class SwiggyInstamartProvider:
    """Grocery search over the Instamart MCP endpoint."""

    name = "swiggy_instamart"
    kind = ProviderKind.GROCERY

    def __init__(self):
        self._client = None
        self._lock = asyncio.Lock()

    async def _session(self):
        """One MCP session, created on first use and reused. Guarded so
        concurrent turns don't open duplicate sessions."""
        async with self._lock:
            if self._client is None:
                self._client = await SwiggyInstamart().initialize()
            return self._client

    async def search(self, query: str, ctx: SearchContext) -> list[Offer]:
        try:
            client = await self._session()
            products = await client.get_product_options(query)
        except Exception as e:
            # Drop the session so the next turn reconnects rather than reusing
            # a dead one. The registry logs and skips us.
            self._client = None
            logger.error(f"[{self.name}] search failed: {e!r}")
            raise

        offers = []
        for product in products[: ctx.limit]:
            if not isinstance(product, dict):
                continue
            price = _to_float(_first(product, "price", "offerPrice", "mrp"))
            if ctx.max_price is not None and price is not None and price > ctx.max_price:
                continue
            title = _first(product, "name", "displayName", "productName")
            if not title:
                continue
            offers.append(
                Offer(
                    provider=self.name,
                    kind=self.kind,
                    id=str(_first(product, "skuId", "id", "productId") or title),
                    title=str(title),
                    venue=_first(product, "brand", "storeName"),
                    price=price,
                    available=bool(_first(product, "inStock", "available") is not False),
                    tags=tuple(filter(None, [_first(product, "quantity", "packSize")])),
                )
            )
        return offers
