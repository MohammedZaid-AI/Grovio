import time
import uuid


# ----------------------------------------------------------------------
# Order lifecycle (Problem 6) — strictly ordered, no state may be skipped.
# ----------------------------------------------------------------------
LIFECYCLE = [
    "draft",
    "selecting_products",
    "cart_ready",
    "awaiting_confirmation",
    "checkout_started",
    "payment_processing",
    "order_placed",
    "awaiting_delivery",
    "delivered",
    "inventory_updated",
    "memory_updated",
]
_LC_INDEX = {name: i for i, name in enumerate(LIFECYCLE)}

# Idle handling (Problems 4 & 7). Tunable so tests can shrink them.
DEFAULT_RESUME_IDLE_SECONDS = 120        # after this gap, offer to resume
DEFAULT_TTL_SECONDS = 60 * 60            # session lives this long since last activity


class ShoppingStateManager:
    """
    THE single source of truth for a shopping conversation.

    One record per phone holds the entire shopping state — conversation state,
    selected/candidate products, the persistent Swiggy MCP session, the cart,
    the order lifecycle, timestamps and the resume/expiry bookkeeping. Nothing
    about a shopping conversation should live anywhere else.

    The historical ``ShoppingSession`` API (start/get/has_session/end/
    get_stage/set_stage/current_item/set_options/select/finished/selected) is
    preserved as a thin facade over this record so existing call-sites keep
    working — ``stage`` is the conversation_state field.
    """

    def __init__(self, resume_idle_seconds=DEFAULT_RESUME_IDLE_SECONDS,
                 ttl_seconds=DEFAULT_TTL_SECONDS):
        self.sessions = {}
        self.resume_idle_seconds = resume_idle_seconds
        self.ttl_seconds = ttl_seconds

    # ------------------------------------------------------------------
    # Lifecycle of the record itself
    # ------------------------------------------------------------------
    def start(self, phone, items):
        now = time.time()
        self.sessions[phone] = {
            # identity / timestamps
            "phone": phone,
            "shopping_id": uuid.uuid4().hex,
            "created_at": now,
            "last_activity": now,
            "expires_at": now + self.ttl_seconds,
            # conversation
            "stage": "planning",              # == conversation_state
            "lifecycle": "draft",
            "mode": None,
            # shopping list / progress
            "items": items,
            "current": 0,                      # current_step
            "selected": [],                    # selected_products
            "options": [],                     # candidate_products (current item)
            # payment / checkout
            "payment_options": [],
            "cart_id": None,
            "swiggy_session": None,            # persistent SwiggyService (in-mem only)
            "checkout_state": None,
            "payment_state": None,
            "approval_state": None,
            "recovery_type": None,
            # resume + override-learning bookkeeping
            "awaiting_resume": False,
            "current_category": None,
            "current_suggested_index": None,
            "current_suggested_name": None,
        }
        return self.sessions[phone]

    def get(self, phone):
        return self.sessions.get(phone)

    def has_session(self, phone):
        return phone in self.sessions

    def end(self, phone):
        return self.sessions.pop(phone, None)

    # ------------------------------------------------------------------
    # Conversation state (a.k.a. stage)
    # ------------------------------------------------------------------
    def get_stage(self, phone):
        return self.sessions[phone]["stage"]

    def set_stage(self, phone, stage):
        self.sessions[phone]["stage"] = stage

    # ------------------------------------------------------------------
    # Persistent Swiggy MCP session (Problem 2)
    # ------------------------------------------------------------------
    def get_service(self, phone):
        """Return the ONE persistent SwiggyService for this conversation,
        creating it once. Reusing the instance reuses the MCP session across
        search -> cart -> payment -> checkout."""
        rec = self.sessions[phone]
        if rec.get("swiggy_session") is None:
            from ai.services.swiggy_service import SwiggyService
            rec["swiggy_session"] = SwiggyService()
        return rec["swiggy_session"]

    # ------------------------------------------------------------------
    # Timestamps / expiry / resume (Problems 4 & 7)
    # ------------------------------------------------------------------
    def touch(self, phone):
        rec = self.sessions.get(phone)
        if not rec:
            return
        now = time.time()
        rec["last_activity"] = now
        rec["expires_at"] = now + self.ttl_seconds

    def idle_gap(self, phone):
        rec = self.sessions.get(phone)
        if not rec:
            return 0
        return time.time() - rec["last_activity"]

    def is_expired(self, phone):
        rec = self.sessions.get(phone)
        if not rec:
            return False
        return time.time() > rec["expires_at"]

    def should_offer_resume(self, phone):
        """True when the user has been away long enough that we should confirm
        before continuing (never auto-restart)."""
        rec = self.sessions.get(phone)
        if not rec:
            return False
        return (not rec["awaiting_resume"]) and self.idle_gap(phone) > self.resume_idle_seconds

    def set_awaiting_resume(self, phone, value):
        self.sessions[phone]["awaiting_resume"] = value

    def is_awaiting_resume(self, phone):
        rec = self.sessions.get(phone)
        return bool(rec and rec["awaiting_resume"])

    # ------------------------------------------------------------------
    # Order lifecycle (Problem 6)
    # ------------------------------------------------------------------
    def lifecycle(self, phone):
        return self.sessions[phone]["lifecycle"]

    def _lc_index(self, phone):
        return _LC_INDEX.get(self.sessions[phone]["lifecycle"], 0)

    def advance_lifecycle(self, phone, target):
        """Advance to `target`. Never moves backward; never skips more than the
        natural order allows (target must be >= current)."""
        if target not in _LC_INDEX:
            raise ValueError(f"Unknown lifecycle state: {target}")
        rec = self.sessions[phone]
        if _LC_INDEX[target] >= _LC_INDEX.get(rec["lifecycle"], 0):
            rec["lifecycle"] = target
        return rec["lifecycle"]

    def begin_checkout(self, phone):
        """Idempotency guard: returns True only for the FIRST checkout attempt.
        A second attempt (e.g. a double 'yes') is rejected (duplicate order
        prevention)."""
        rec = self.sessions.get(phone)
        if not rec:
            return False
        if self._lc_index(phone) >= _LC_INDEX["checkout_started"]:
            return False
        rec["lifecycle"] = "checkout_started"
        rec["checkout_state"] = "started"
        return True

    # ------------------------------------------------------------------
    # Current item / candidate products
    # ------------------------------------------------------------------
    def current_item(self, phone):
        session = self.sessions[phone]
        if session["current"] >= len(session["items"]):
            return None
        return session["items"][session["current"]]

    def set_options(self, phone, options):
        self.sessions[phone]["options"] = options

    def set_selection_context(self, phone, category, suggested_index, suggested_name):
        """Recorded by the ranker before the user chooses, so a manual override
        can be detected and learned (Problem 5)."""
        rec = self.sessions[phone]
        rec["current_category"] = category
        rec["current_suggested_index"] = suggested_index
        rec["current_suggested_name"] = suggested_name

    # ------------------------------------------------------------------
    # Product selection (+ override learning, Problem 5)
    # ------------------------------------------------------------------
    def select(self, phone, index):
        session = self.sessions[phone]
        product = session["options"][index]
        quantity = session["items"][session["current"]]["quantity"]
        variant = product["variations"][0]
        pack_size = variant.get("quantityDescription")

        session["selected"].append(
            {
                "displayName": product["displayName"],
                "spinId": variant["spinId"],
                # Swiggy's update_cart REQUIRES skuId alongside spinId; without
                # it the item is silently rejected and the cart stays empty.
                "skuId": variant.get("skuId") or product.get("skuId"),
                "quantity": quantity,
                "price": variant["price"]["offerPrice"],
                "pack_size": pack_size,
                "category": session.get("current_category"),
            }
        )

        # Override learning: the user chose a product other than the one the
        # ranker put on top for this item.
        suggested_index = session.get("current_suggested_index")
        category = session.get("current_category")
        if (suggested_index is not None and index != suggested_index and category):
            self._record_override(
                category=category,
                suggested=session.get("current_suggested_name"),
                chosen=product["displayName"],
                chosen_pack=pack_size,
            )

        # advance progress + lifecycle, reset per-item context
        session["current"] += 1
        session["options"] = []
        session["current_category"] = None
        session["current_suggested_index"] = None
        session["current_suggested_name"] = None
        self.advance_lifecycle(phone, "selecting_products")

    def _record_override(self, category, suggested, chosen, chosen_pack):
        try:
            from ai.memory.restaurant_memory import restaurant_memory
            restaurant_memory.record_override(
                category=category,
                suggested=suggested,
                chosen=chosen,
                chosen_pack=chosen_pack,
            )
        except Exception as e:
            print(f"[ShoppingState] override learning skipped: {e}")

    def finished(self, phone):
        session = self.sessions[phone]
        return session["current"] >= len(session["items"])

    def selected(self, phone):
        return self.sessions[phone]["selected"]


def _format_candidates(item_name, products):
    """Shared candidate-selection prompt so the initial presentation and the
    resume re-render are byte-for-byte identical. Option 1 is the ranker's
    recommendation."""
    shown = products[:5]
    reply = [f"Choose {item_name}", ""]
    for i, product in enumerate(shown, start=1):
        variant = product["variations"][0]
        star = "  ⭐ recommended" if i == 1 else ""
        reply.append(
            f"{i}. {product['displayName']} "
            f"({variant['quantityDescription']}) "
            f"₹{variant['price']['offerPrice']}{star}"
        )
    reply.append("")
    reply.append(f"Reply with a number (1-{len(shown)}) to choose.")
    return "\n".join(reply)


# Single shared instance. Name preserved for backward compatibility.
shopping_session = ShoppingStateManager()
