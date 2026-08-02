"""
Provider account-linking HTTP surface.

One route: the OAuth callback the provider redirects to. It is deliberately
separate from the WhatsApp webhook — different trust model, different failure
modes, and it is the one URL a provider must whitelist.

The user sees a plain confirmation page here; the real continuation happens back
in WhatsApp, where their original request is answered without them repeating it.
"""
import hashlib

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

import db
from ai import identity
from ai.providers import oauth, registry
from core.logger import logger

router = APIRouter()

_PAGE = """<!doctype html>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         display: grid; place-items: center; min-height: 100vh; margin: 0;
         background: #faf9f7; color: #1a1a1a; }}
  .card {{ max-width: 22rem; padding: 2.5rem 2rem; text-align: center; }}
  .mark {{ font-size: 2.5rem; line-height: 1; }}
  h1 {{ font-size: 1.25rem; margin: 1rem 0 .5rem; }}
  p {{ margin: 0; color: #5c5c5c; line-height: 1.5; font-size: .95rem; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #17171a; color: #f2f2f2; }}
    p {{ color: #a8a8a8; }}
  }}
</style>
<div class="card">
  <div class="mark">{mark}</div>
  <h1>{heading}</h1>
  <p>{body}</p>
</div>
"""


def _page(mark, heading, body, status=200, title="Account linking"):
    return HTMLResponse(
        _PAGE.format(mark=mark, heading=heading, body=body, title=title),
        status_code=status,
    )


async def _resume_conversation(phone: str, pending_message: str) -> None:
    """Re-queue what the user originally asked for.

    Pushing it back through the normal inbound path means the reply is produced,
    ordered, retried and delivered by exactly the same machinery as any other
    message — no second code path to keep correct.
    """
    if not pending_message or not pending_message.strip():
        return

    from backend.whatsapp_worker import enqueue_and_wake

    try:
        # Deterministic id so the queue's dedup still holds if a provider
        # delivers the same callback twice. sha256, NOT hash(): Python salts
        # hash() per process, so a restart would produce a different id for the
        # same message and the duplicate would slip through.
        digest = hashlib.sha256(f"{phone}:{pending_message}".encode()).hexdigest()[:16]
        await enqueue_and_wake(
            message_sid=f"resume-{digest}",
            phone=phone,
            body=pending_message,
            num_media=0,
        )
    except Exception:
        logger.error("[linking] could not resume conversation", exc_info=True)


@router.get("/link/{provider}/callback")
async def oauth_callback(provider: str, request: Request):
    """Where the provider sends the user back after they authorise us."""
    params = request.query_params

    # The provider can also report a refusal here.
    if params.get("error"):
        logger.info(f"[linking] provider reported error: {params.get('error')}")
        return _page(
            "✕", "Not connected",
            "No problem — head back to WhatsApp and just ask again whenever you're ready.",
            status=400,
        )

    try:
        result = await oauth.complete(
            state=params.get("state", ""),
            code=params.get("code", ""),
            config_for=registry.oauth_config_for,
        )
    except oauth.OAuthError as e:
        # Never echo provider errors or the state back to the browser.
        logger.warning(f"[linking] callback rejected: {e}")
        return _page(
            "⏳", "This link didn't work",
            "It may have expired or already been used. Go back to WhatsApp and "
            "ask again — I'll send you a fresh one.",
            status=400,
        )
    except Exception:
        logger.error("[linking] callback failed", exc_info=True)
        return _page(
            "✕", "Something went wrong",
            "Please head back to WhatsApp and try connecting again.",
            status=500,
        )

    identity.mark_linked(result["phone"])
    db.delete_expired_oauth_states()
    await _resume_conversation(result["phone"], result["pending_message"])

    label = getattr(registry.get(result["provider"]), "display_name", result["provider"])
    body = (
        "You're all set. Head back to WhatsApp — I'm already picking up where we left off."
        if result["pending_message"]
        else "You're all set. Head back to WhatsApp and tell me what you feel like eating."
    )
    return _page("✓", f"{label} connected", body)
