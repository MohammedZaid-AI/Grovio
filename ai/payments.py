"""
Waiting for a payment to land.

Some providers create an order in a pending state and hand back a hosted
payment page. The order is not real until the user pays, and that happens on
THEIR clock — long after the turn that started it has ended.

So this watches in the background and speaks up when it resolves. It exists as
its own module because the alternative is holding a WhatsApp turn open for
minutes, which would freeze that person's conversation.

WHAT IT WILL NOT DO
  * Never claim an order is placed on anything but the provider saying so. A
    poll it could not read is `pending`, never success.
  * Never handle card, UPI or account details. The only thing that reaches the
    user is the provider's own payment URL.
  * Never spend anything. The order already exists; this only observes it.
"""
import asyncio

import db
from core.logger import logger

from ai import conversation
from ai.providers import base

# Swiggy long-polls (~19s per call) and returns its own cadence. These apply
# only when a provider tells us nothing.
DEFAULT_INTERVAL_SECONDS = 5
DEFAULT_TIMEOUT_SECONDS = 8 * 60

# The provider's own vocabulary, grouped by what it means for us.
PAID = ("success", "paid", "captured", "completed")
FAILED = ("failed", "failure", "declined")
ABANDONED = ("cancelled", "canceled", "expired", "refund-initiated", "refunded")
CART_CHANGED = ("cart_changed", "cart-changed")

_watchers: dict = {}


def _seconds(milliseconds, fallback):
    if not milliseconds:
        return fallback
    try:
        return max(1.0, float(milliseconds) / 1000.0)
    except (TypeError, ValueError):
        return fallback


async def _notify(phone: str, text: str) -> None:
    """Say something to a user outside any turn, via the durable queue."""
    from backend.whatsapp_worker import deliver

    try:
        await deliver(phone, text)
    except Exception:
        logger.error(f"[payments] could not deliver an update to {phone}", exc_info=True)


async def _watch(user_phone: str, provider, order, ctx, title: str, row_id) -> None:
    interval = _seconds(order.poll_interval_ms, DEFAULT_INTERVAL_SECONDS)
    deadline = _seconds(order.poll_timeout_ms, DEFAULT_TIMEOUT_SECONDS)
    waited = 0.0

    logger.info(
        f"[payments] watching {order.order_id} for {user_phone} — every "
        f"{interval:g}s for up to {deadline:g}s"
    )

    while waited < deadline:
        await asyncio.sleep(interval)
        waited += interval

        try:
            status = (await provider.payment_status(order, ctx) or "pending").lower()
        except Exception:
            logger.error(f"[payments] poll raised for {order.order_id}", exc_info=True)
            status = "pending"

        if status in PAID:
            # Finalise, then say so. Confirming first means we never tell
            # someone their food is coming while the provider still thinks the
            # order is half-made.
            confirmed = True
            try:
                confirmed = await provider.confirm_payment(order, ctx)
            except Exception:
                logger.error(f"[payments] confirm raised for {order.order_id}", exc_info=True)
                confirmed = False

            _finish(user_phone, row_id, base.PLACED if confirmed else base.UNKNOWN)
            if confirmed:
                await _notify(user_phone, f"Paid and confirmed — {title} is on its way 🎉")
            else:
                await _notify(
                    user_phone,
                    f"Your payment for {title} went through, but I couldn't get the "
                    f"final confirmation. Please check the Swiggy app before "
                    f"reordering, so you don't pay twice."
                )
            return

        if status in ABANDONED:
            _finish(user_phone, row_id, base.CANCELLED)
            await _notify(user_phone, f"That order for {title} was cancelled. "
                                      f"Nothing more will be charged.")
            return

        if status in FAILED:
            _finish(user_phone, row_id, base.CANCELLED)
            await _notify(user_phone, f"The payment for {title} didn't go through, so "
                                      f"nothing was charged and no order was placed. "
                                      f"Say the word and I'll set it up again.")
            return

        if status in CART_CHANGED:
            _finish(user_phone, row_id, base.CANCELLED)
            await _notify(user_phone, f"Something changed in the basket for {title}, so "
                                      f"I stopped rather than order the wrong thing. "
                                      f"Want me to look again?")
            return

    # Ran out of patience. The order may still be paid, so this must NOT claim
    # either outcome — a wrong "it failed" invites a duplicate order.
    logger.warning(f"[payments] gave up waiting on {order.order_id} after {waited:g}s")
    _finish(user_phone, row_id, base.UNKNOWN)
    await _notify(
        user_phone,
        f"I haven't seen the payment for {title} come through yet. If you did pay, "
        f"it'll show in your Swiggy app — please check there before ordering again."
    )


def _finish(phone: str, row_id, status: str) -> None:
    """Close the books. Bookkeeping only: never let it raise into the watcher."""
    try:
        if row_id:
            db.update_order_status(row_id, status)
    except Exception:
        logger.error("[payments] could not update the order row", exc_info=True)

    try:
        if status == base.PLACED:
            conversation.order_succeeded(phone)
        else:
            conversation.cancel_pending(phone)
    except Exception:
        logger.error("[payments] could not close the conversation", exc_info=True)

    _watchers.pop(phone, None)


def watch(user_phone: str, provider, order, ctx, title: str, row_id=None) -> None:
    """Start watching a pending payment. Returns immediately.

    One watcher per phone: a second pending payment for the same person would
    mean two orders in flight, which the conversation state does not allow.
    """
    existing = _watchers.get(user_phone)
    if existing and not existing.done():
        logger.info(f"[payments] already watching a payment for {user_phone}")
        return

    task = asyncio.create_task(
        _watch(user_phone, provider, order, ctx, title, row_id)
    )
    _watchers[user_phone] = task
    # Without a reference the task can be garbage collected mid-flight, and a
    # failure inside it would be swallowed silently.
    task.add_done_callback(lambda t: _watchers.pop(user_phone, None))
