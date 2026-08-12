from dotenv import load_dotenv
load_dotenv()

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

import db
import ai.providers
from backend.linking import router as linking_router
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

    if whatsapp.PROVIDER == "local":
        # Deliberate for development, and needs saying every single boot: this
        # transport is unofficial and there is no webhook signature to verify.
        logger.warning(
            "WHATSAPP_PROVIDER=local — using an unofficial WhatsApp Web session. "
            "DEVELOPMENT ONLY: the number can be banned, and inbound messages are "
            "NOT cryptographically verified. Set WHATSAPP_PROVIDER=cloud for "
            "production."
        )
    else:
        missing = [
            name for name in ("WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID",
                              "WHATSAPP_APP_SECRET", "WHATSAPP_VERIFY_TOKEN")
            if not os.getenv(name)
        ]
        if missing:
            # The last two fail CLOSED, so this is not cosmetic: without them
            # every inbound webhook is rejected and the product is silent.
            logger.warning(
                f"WhatsApp Cloud API is incompletely configured — missing "
                f"{', '.join(missing)}. Webhook verification and signature checks "
                f"fail closed, so inbound messages will be REJECTED until these are set."
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
def home():
    return {"status": "running", "service": "AI Food Concierge"}


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
    checks["messaging_api_version"] = whatsapp.api_version()

    ready = (checks["database"] == "ok" and checks["encryption"] == "ok"
             and checks["messaging_configured"])
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"ready": ready, "checks": checks},
    )
