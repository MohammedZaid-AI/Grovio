from dotenv import load_dotenv
load_dotenv()

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

import db
import ai.providers
from backend.linking import complete_callback, router as linking_router
from backend.routes import router
from backend.whatsapp_worker import recover_pending
from core import crypto
from core.logger import logger
import whatsapp


def _startup_warnings():
    """Say loudly, once, when the process is misconfigured for real users.

    These are warnings rather than hard failures so local development stays
    friction-free — but they name the exact risk so it cannot be missed in a
    deploy log.
    """
    if not crypto.is_configured():
        logger.warning(
            "TOKEN_ENCRYPTION_KEY is not set — provider accounts CANNOT be linked. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        )

    # Deliberate for this prototype, and worth saying every single boot: the
    # transport is an unofficial WhatsApp Web client, so the number can be
    # banned and inbound messages carry no cryptographic signature. The shared
    # secret is the ONLY thing making the gateway trusted.
    logger.warning(
        "WhatsApp transport is the Baileys gateway — an UNOFFICIAL WhatsApp Web "
        "protocol client, not the Meta Business Cloud API. Use a spare number, "
        "never one tied to a business account."
    )
    if not os.getenv("WHATSAPP_GATEWAY_SECRET"):
        logger.warning(
            "WHATSAPP_GATEWAY_SECRET is not set. This FAILS CLOSED both ways: "
            "every inbound message from the gateway is rejected AND every "
            "outgoing send fails, so the product is silent until it is set."
        )

    base_url = os.getenv("PUBLIC_BASE_URL", "")
    if not base_url:
        logger.warning(
            "PUBLIC_BASE_URL is not set — OAuth callbacks will point at localhost "
            "and account linking will fail for real users."
        )
    elif base_url.startswith("http://localhost"):
        # Deliberate and supported: providers allowlist http://localhost for
        # development. Only the OAuth callback uses this — the WhatsApp webhook
        # is configured separately, so a tunnel can serve that at the same time.
        logger.info(
            f"PUBLIC_BASE_URL is {base_url} — development mode. Open link URLs "
            "in a browser ON THIS MACHINE; they will not work from a phone."
        )
    elif not base_url.startswith("https://"):
        logger.warning(
            f"PUBLIC_BASE_URL is not https ({base_url}) — providers reject "
            "non-https redirect URIs in production."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure the schema exists, wire up the available platforms, then recover
    # anything left in flight by a previous run so a restart never loses a reply.
    db.init_db()
    _startup_warnings()
    ai.providers.setup()
    await recover_pending()
    # Last, so recovery is done before new messages can arrive. The Cloud API
    # is stateless and this is a no-op; the local transport opens its socket
    # here and may print a QR to scan.
    await whatsapp.start()
    yield
    await whatsapp.stop()


app = FastAPI(title="Food Concierge", lifespan=lifespan)

app.include_router(router)
app.include_router(linking_router)


@app.get("/")
async def home(request: Request):
    """Status — or an OAuth callback that landed here instead of on its path.

    A provider whose redirect allowlist holds a bare origin (`http://localhost`
    for development, say) can send the user back to the root rather than to the
    callback URL we asked for. The authorization code is still in the query
    string, and dropping it means the link silently never completes — which is
    exactly what was happening. `state` identifies the provider, so finishing
    the link here is equivalent.
    """
    params = request.query_params
    if params.get("code") and params.get("state"):
        logger.info("[linking] OAuth callback arrived at the site root")
        return await complete_callback(params)
    if params.get("error"):
        return await complete_callback(params)

    return JSONResponse({"status": "running", "service": "AI Food Concierge"})


@app.get("/health")
def health():
    """Readiness probe for deploys and uptime checks.

    Reports what is actually wired up rather than just returning 200: a green
    process with an unreachable database or no encryption key is not ready to
    take a user's credentials.
    """
    checks = {}
    try:
        db.get_connection().execute("SELECT 1").fetchone()
        checks["database"] = "ok"
    except Exception:
        logger.error("[health] database check failed", exc_info=True)
        checks["database"] = "error"

    checks["encryption"] = "ok" if crypto.is_configured() else "unconfigured"
    checks["providers"] = len(ai.providers.registry.available_kinds())
    checks["messaging"] = whatsapp.PROVIDER
    checks["messaging_configured"] = whatsapp.is_configured()
    checks["messaging_gateway"] = whatsapp.gateway_url()

    ready = (checks["database"] == "ok" and checks["encryption"] == "ok"
             and checks["messaging_configured"])
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"ready": ready, "checks": checks},
    )
