import json
from datetime import datetime, timezone
from pathlib import Path


# One category's learning profile. Preferences (preferred brand / pack / supplier)
# are DERIVED from these counts at read time, so they self-correct as behaviour
# changes. Nothing here is hardcoded and no alias tables are used — categories
# and brands are produced by the semantic (LLM) layer, never by string rules.
EMPTY_CATEGORY_PROFILE = {
    "brand_counts": {},        # brand -> times purchased
    "pack_counts": {},         # pack size -> times purchased
    "supplier_counts": {},     # supplier -> times purchased
    "favorite_products": {},   # display name -> times purchased
    "rejected": [],            # products/brands the user passed over
    "overrides": [],           # {"suggested": .., "chosen": ..}
    "purchase_count": 0,
    "last_purchased_at": None,
}

MAX_REJECTED = 25
MAX_OVERRIDES = 25


class RestaurantMemory:
    """
    Category-based long-term restaurant preference memory.

    Schema:
        {
          "categories": {
             "<category>": { ...EMPTY_CATEGORY_PROFILE... }
          }
        }

    The previous implementation stored ``preferred_brands[full_display_name] =
    first_word``, which is semantically meaningless and self-poisoning. This
    version keys everything by an inferred CATEGORY (e.g. "cola") and derives
    the preferred brand/pack/supplier from purchase frequency, so future
    selections keep improving automatically.
    """

    def __init__(self):
        self.file = Path("data/restaurant_memory.json")
        self.file.parent.mkdir(parents=True, exist_ok=True)
        if not self.file.exists():
            self.save({"categories": {}})

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def load(self):
        try:
            with open(self.file, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if not content:
                data = {"categories": {}}
                self.save(data)
                return data
            data = json.loads(content)
        except Exception:
            data = {"categories": {}}
            self.save(data)
            return data

        # Migrate/reset any legacy (pre-category, polluted) schema.
        if not isinstance(data, dict) or "categories" not in data:
            data = {"categories": {}}
            self.save(data)
        return data

    def save(self, data):
        with open(self.file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

    def get(self):
        return self.load()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _norm(self, s):
        return (s or "").strip().lower()

    def _category(self, data, category):
        cat = self._norm(category) or "uncategorized"
        cats = data.setdefault("categories", {})
        if cat not in cats:
            cats[cat] = json.loads(json.dumps(EMPTY_CATEGORY_PROFILE))
        return cats[cat]

    def _argmax(self, counts):
        if not counts:
            return None
        return max(counts.items(), key=lambda kv: kv[1])[0]

    # ------------------------------------------------------------------
    # Learning events
    # ------------------------------------------------------------------
    def record_purchase(self, category, brand=None, product=None,
                        pack_size=None, supplier=None):
        """Reinforce a successful purchase for its category."""
        data = self.load()
        prof = self._category(data, category)
        if brand:
            prof["brand_counts"][brand] = prof["brand_counts"].get(brand, 0) + 1
        if pack_size:
            prof["pack_counts"][pack_size] = prof["pack_counts"].get(pack_size, 0) + 1
        if supplier:
            prof["supplier_counts"][supplier] = prof["supplier_counts"].get(supplier, 0) + 1
        if product:
            prof["favorite_products"][product] = prof["favorite_products"].get(product, 0) + 1
        prof["purchase_count"] += 1
        prof["last_purchased_at"] = datetime.now(timezone.utc).isoformat()
        self.save(data)

    def record_override(self, category, suggested, chosen,
                        chosen_brand=None, chosen_pack=None):
        """The user picked something different from what Grovio auto-suggested.
        This is the strongest possible preference signal."""
        data = self.load()
        prof = self._category(data, category)
        prof["overrides"].append({"suggested": suggested, "chosen": chosen})
        prof["overrides"] = prof["overrides"][-MAX_OVERRIDES:]
        # Reinforce the chosen product harder than a normal purchase. The brand
        # may be unknown at selection time (we never string-parse it) — the
        # trainer sets brand at purchase — but the chosen PRODUCT itself is a
        # strong, immediate preference signal.
        if chosen:
            prof["favorite_products"][chosen] = prof["favorite_products"].get(chosen, 0) + 2
        if chosen_brand:
            prof["brand_counts"][chosen_brand] = prof["brand_counts"].get(chosen_brand, 0) + 2
        if chosen_pack:
            prof["pack_counts"][chosen_pack] = prof["pack_counts"].get(chosen_pack, 0) + 1
        # The suggested-but-rejected product is a negative signal.
        if suggested and suggested not in prof["rejected"]:
            prof["rejected"].append(suggested)
            prof["rejected"] = prof["rejected"][-MAX_REJECTED:]
        self.save(data)

    def record_rejection(self, category, product):
        """Record a product the user explicitly declined."""
        data = self.load()
        prof = self._category(data, category)
        if product and product not in prof["rejected"]:
            prof["rejected"].append(product)
            prof["rejected"] = prof["rejected"][-MAX_REJECTED:]
        self.save(data)

    # ------------------------------------------------------------------
    # Derived reads (used by the semantic ranker)
    # ------------------------------------------------------------------
    def preferred_brand(self, category):
        prof = self._category(self.load(), category)
        return self._argmax(prof["brand_counts"])

    def category_profile(self, category):
        """Compact, derived profile for one category."""
        prof = self._category(self.load(), category)
        favs = sorted(prof["favorite_products"].items(),
                      key=lambda kv: kv[1], reverse=True)
        return {
            "category": self._norm(category) or "uncategorized",
            "preferred_brand": self._argmax(prof["brand_counts"]),
            "preferred_pack_size": self._argmax(prof["pack_counts"]),
            "preferred_supplier": self._argmax(prof["supplier_counts"]),
            "favorite_products": [name for name, _ in favs[:5]],
            "rejected": prof["rejected"][-10:],
            "purchase_count": prof["purchase_count"],
            "last_purchased_at": prof["last_purchased_at"],
        }

    def summary(self):
        """Compact view of every category with signal — fed to the ranker."""
        data = self.load()
        out = {}
        for cat in list(data.get("categories", {}).keys()):
            p = self.category_profile(cat)
            if p["preferred_brand"] or p["favorite_products"] or p["rejected"]:
                out[cat] = {
                    "preferred_brand": p["preferred_brand"],
                    "preferred_pack_size": p["preferred_pack_size"],
                    "preferred_supplier": p["preferred_supplier"],
                    "favorite_products": p["favorite_products"],
                    "rejected": p["rejected"],
                    "purchase_count": p["purchase_count"],
                }
        return out


restaurant_memory = RestaurantMemory()
