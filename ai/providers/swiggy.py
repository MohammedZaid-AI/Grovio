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
from ai.providers.oauth import OAuthConfig
from integrations.swiggy.swiggy_mcp import SwiggyInstamart

# Instamart MCP endpoint. Swiggy also runs /food and /dineout — see
# FEASIBILITY.md; adding them means new adapters here, nothing above.
INSTAMART_SERVER = "https://mcp.swiggy.com/im"


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
    """Grocery search over the Instamart MCP endpoint.

    Acts on behalf of an individual user, so it is a LinkableProvider: it
    declares where its OAuth server lives and lets the generic engine do the
    rest. Endpoints are DISCOVERED per RFC 8414/9728 — none are hardcoded here,
    because inventing them would be inventing an API.

    SWIGGY_OAUTH_CLIENT_ID is issued with Builders Club production access. Until
    then discovery may offer dynamic registration, or local prototyping runs
    against a whitelisted localhost redirect. See FEASIBILITY.md.
    """

    name = "swiggy_instamart"
    display_name = "Swiggy"
    kind = ProviderKind.GROCERY

    oauth = OAuthConfig(
        server_url=INSTAMART_SERVER,
        client_id_env="SWIGGY_OAUTH_CLIENT_ID",
        client_secret_env="SWIGGY_OAUTH_CLIENT_SECRET",
    )

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
