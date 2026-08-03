# Ordering regressions — what broke in the pivot, and how it was restored

## R0 — The order was placed, and the user was told it failed

**The live bug.** Reported as: Instamart search works, products display with
prices, user replies `1`, bot says *"Something went wrong on my end — mind
trying that again?"* — **while the order appears in their Swiggy account.**

### The exact line

```
ai/skills.py  _execute_pending()      order_id = db.save_order(...)
  └─ db.py:806  save_order()          INSERT INTO orders (phone, provider, ...)
       └─ sqlite3.OperationalError: table orders has no column named provider
```

### Why

The live `database/orders.db` still had the **ERP's** `orders` table:

```
id · product_name · spin_id · quantity · order_type · schedule_time
recurrence · status · order_id · items · total · phone · created_at
```

`init_db()` creates the concierge's `orders` table with
`CREATE TABLE IF NOT EXISTS`. The table already existed, so the statement was a
no-op and the new schema was **never applied**. Phase 2 deleted the ERP's *code*
but not its *tables*, and `IF NOT EXISTS` hid that for four phases — because
nothing had ever successfully placed an order against this database before.

### Why it surfaced as a generic error

`save_order` runs **after** `provider.place()` returns, outside the `try` that
guards it. Nothing else catches it on that path:

```
concierge.respond()          except Exception -> ERROR_REPLY   ← the user's message
  plan()                     no guard
    _resolve_state_first()   no guard   ← the deterministic selection path
      skills.place_order()   no guard
        _execute_pending()   guards provider.place() only
          db.save_order()    raises
```

`_dispatch` has a try/except, but a resolved selection never goes through
`_dispatch` — it is handled in code before the model is consulted.

Two further consequences: `conversation.order_succeeded()` never ran, so the
conversation was stranded in `ORDERING` with five live offers and a pending
order for milk **that had already been delivered**. Replying `1` again would
have placed a second real order.

### Fixed three ways

1. **Root cause** — `_retire_legacy_orders_table()` in `db.py` detects a
   pre-pivot `orders` table (no `provider_order_id`) and renames it to
   `orders_legacy_erp` before the concierge's table is created. Renamed, not
   dropped: destroying someone's rows to fix a schema is not our call. Idempotent.
2. **Structural** — past `provider.place()` the order **exists**. Saving the
   row, closing the conversation and noting the food are bookkeeping: each is
   wrapped, failures are logged with a full traceback, and the user is still
   told their order was placed. Bookkeeping must never speak for the provider.
3. **Recovery** — `ORDERING → AWAITING_SELECTION / RECOMMENDING` added to
   `ALLOWED`. Without it a turn that died mid-order stranded the conversation
   permanently: every later search raised `IllegalTransition`.

Logging now covers the whole path — spinId, skuId, quantity, the `update_cart`
payload and response, the `get_cart` body, the checkout payload and the
**complete** checkout response including `structuredContent`.

**Tests:** `test_ordering_flow.py` §11 (16 checks — the migration, and each of
the three bookkeeping steps failing without the user ever being told it failed),
§12 (recovery from a stranded `ORDERING`).

**Live database:** migrated, 11 ERP rows preserved in `orders_legacy_erp`,
backup at `database/orders_backup_pre_schema_fix.db`. The stranded conversation
was cleared and the milk order recorded with an empty provider order id — it
was never returned to us, so it is not invented.

---


The ERP→concierge pivot deleted `ai/services/swiggy_service.py` and
`ai/shopping/shopping_session.py` in Phase 2, and Phase 5 rewrote ordering from
the raw MCP client. The rewrite was structurally better and factually worse: it
lost seven pieces of hard-won knowledge about what Swiggy actually returns.

Method: diff the current implementation against `06a1204^` — the last commit
before the delete — not against what the code *looks like* it should do.

```bash
git show 06a1204^:ai/services/swiggy_service.py
git show 06a1204^:ai/shopping/shopping_session.py
```

Every regression below is now pinned by a test that fails if it returns.

---

## R1 — A placed order was reported as failed

**Severity: highest. This one could double-charge someone.**

| | |
|---|---|
| **Before** | `_parse_success` was only ever reached when `isError` was False. Its comment: *"the order is already CONFIRMED. This method therefore NEVER throws and always reports order_placed=True."* A missing id downgraded `success`, never `order_placed`. |
| **After the pivot** | `if not order_id: raise ProviderError("checkout returned no order id")` |
| **Why it broke** | The rewrite treated "I couldn't parse an id" as "the order didn't happen". Those are different facts. Swiggy had already accepted the order. |
| **Impact** | Order placed → we say "that failed, shall I try again?" → user says yes → **second real order**. |
| **Restored** | A non-error checkout always returns `PlacedOrder(status=PLACED)`. A missing id is logged with the payload keys and left empty — never invented. |
| **Tests** | `test_ordering_flow.py` §4 — *accepted-but-unparseable is PLACED, not an exception* (both providers) |

## R2 — The order id was thrown away by the `data` unwrap

| | |
|---|---|
| **Before** | `_extract_order_fields` read from **both** the top level and the nested `data` object: `sources = [data]; if inner: sources.append(inner)`. |
| **After the pivot** | `return structured.get("data") if isinstance(...) else structured` — one **or** the other. |
| **Why it broke** | Swiggy wraps the body under `data` but leaves the order id at the top level. Choosing `data` discards it. |
| **Impact** | Fed straight into R1: no id → "order failed". |
| **Restored** | `_flatten()` merges the two (`{**outer, **inner}`) in both providers. |
| **Tests** | §4 — *id at the top level survives the `data` unwrap* |

## R3 — `cartTotal` was dropped, so confirmations had no amount

| | |
|---|---|
| **Before** | `"total": ("cartTotal", "orderTotal", "grandTotal", "total", "amount")` — `cartTotal` first. |
| **After the pivot** | `("total", "grandTotal", "orderTotal")` — the actual key is absent. |
| **Impact** | "Total: amount not returned" on a successful order. |
| **Restored** | The full pre-pivot key list, in the original order. The confirmation also falls back to the price the user was just shown. |
| **Tests** | §3 — *cartTotal is read as the total* (both providers) |

## R4 — `update_cart` silently added nothing without `skuId`

Fixed in `f1b1b8f`, listed for completeness. The pre-pivot comment was explicit:

> *Swiggy's `update_cart` requires `skuId` per item; omitting it makes the add
> silently fail and leaves the cart empty (→ "cart not found" at checkout).*

Restored via a composite `Offer.id` (`spinId::skuId`) that only the provider
parses, so the layers above stay provider-agnostic.
**Tests:** `test_swiggy_payload.py` §2.

## R5 — Price is a nested object on the variant, not a number on the product

| | |
|---|---|
| **Before** | `variant["price"]["offerPrice"]` — variant, then the nested object. |
| **After the pivot** | `_to_float(product.get("price"))` → `None` on a dict. |
| **Impact** | Every option rendered with no ₹ at all. |
| **Restored** | `_price_of()` reads the **variant first** (the pack being bought), then unwraps the price object. Same treatment added to the restaurant provider, which had the identical hazard. |
| **Tests** | §2 — *variant price WINS over a product-level one*; `test_swiggy_payload.py` §1 |

`f1b1b8f` fixed the nesting but resolved it product-first. The variant is
authoritative for the same reason it is for `skuId`: it is the thing in the cart.

## R6 — The pack size vanished from grocery listings

| | |
|---|---|
| **Before** | `variant["quantityDescription"]` — "500 ml". |
| **After the pivot** | `_pick(product, "quantity", "packSize", "weight")` — Instamart's real field is not in that list. |
| **Impact** | "Amul Taaza Milk · ₹33" with no pack size. Not a choosable option — ₹33 for what? |
| **Restored** | `quantityDescription` added, variant-first, and `Offer.summary()` now emits `tags` so it reaches the user at all. |
| **Tests** | §2 — *pack size (quantityDescription) survives* |

## R7 — Every failure looked the same, so sold-out items were retried three times

| | |
|---|---|
| **Before** | Checkout errors were classified from Swiggy's prose into `OUT_OF_STOCK`, `STORE_UNAVAILABLE`, `LIMIT_EXCEEDED`, `PARTIAL_AVAILABILITY`. |
| **After the pivot** | `raise ProviderError("checkout failed")` for everything. |
| **Impact** | An out-of-stock dish fails identically on every attempt. The user was asked "shall I retry?" twice for something that could not possibly succeed. |
| **Restored** | `_is_item_problem()` maps that vocabulary to `ItemUnavailable`, which the skills layer already handles differently: it offers alternatives instead of a pointless retry. |
| **Tests** | §5 — *out-of-stock raises ItemUnavailable, not a generic error* |

---

## Not a pivot regression, but broken

### Restaurant search dropped every grouped result

`swiggy_food.py` required `restaurantId` and `itemId` on each entry's top
level. Swiggy also returns restaurant *cards* holding their matching dishes,
where the restaurant id is on the card and the rating and ETA describe the
restaurant, not the dish. Every result of that shape was skipped — which reads
to the user as "nothing found".

`_entries()` now flattens both shapes into `(dish, restaurant)` pairs, and
rating/ETA/venue fall back to the restaurant. When entries come back but none
are orderable, it logs the actual keys rather than shrugging.
**Tests:** §1 — *restaurant-grouped cards are flattened, not dropped*.

### A selection could still trigger a search

`_resolve_state_first` only resolved a number when the state was exactly
`AWAITING_SELECTION`. After retries were exhausted the state is `ORDER_FAILED`
while the list is still on screen — so "2" fell through to the model, which
searched again. Now **any** state with live offers resolves a selection in code.

`ALLOWED` gained `ORDER_FAILED → ORDERING` and `ORDER_COMPLETE → ORDERING`,
because picking another option off a list you can still see is normal.
**Tests:** §9 — *option 2 was ordered straight from the dead list*.

### A retry rebuilt the wrong kind of offer

`retry_pending_order`'s fallback hardcoded `kind: RESTAURANT`, mislabelling
every grocery retry. `PendingOrder` now carries `kind`.
**Tests:** §7 — *the KIND is remembered too* (both providers).

---

## What was tried and removed

Resolving a selection by **name** ("go with Meghana") in code. It matched
`"need milk"` against an offer titled *Milk* and ordered it instead of
searching. A false positive here spends money on the wrong thing, so naming an
option goes through the model, which calls `place_order` with the number.
The comment in `conversation.resolve_selection` records this so it isn't
re-attempted.

---

## Coverage

| Suite | Checks | Protects |
|---|---|---|
| `test_ordering_flow.py` | 95 | search → list → selection → order → retry, **both providers**; R1, R2, R3, R5, R6, R7 |
| `test_swiggy_payload.py` | 29 | the exact cart payload; R4, R5 |
| `test_conversation_state.py` | 97 | the state machine, TTL, retry bounds |
| `test_journey.py` | 69 | the end-to-end journey through the planner |
| **Total across the repo** | **518** | |

The flow tests assert on a **spy provider**: a selection that triggers a search
fails the build, because `spy.searches` is checked to be 0 on every path —
after a selection, after a retry, and after picking a second option from a dead
list.

---

## Still unverified

**Restaurant price units.** Swiggy's menu payloads are widely reported to carry
paise (`34000` = ₹340). Extraction is now robust, but the *unit* cannot be
confirmed from here — this sandbox cannot reach Swiggy. Dividing by 100 on a
guess would show ₹3.40 for a real ₹340, so nothing was guessed.

Resolve it with the read-only probe, which places no order and builds no cart:

```
venv/Scripts/python.exe scripts/probe_food.py
```

If prices come back in paise, that is a one-line change in `_price_of` — and
the log line `N/M dishes had no readable price. Item keys: [...]` will name the
right field if the key list is wrong instead.
