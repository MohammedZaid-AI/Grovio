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
from whatsapp.transport import TRANSPORT


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

    base_url = os.getenv("PUBLIC_BASE_URL", "")
    if not base_url:
        logger.warning(
            "PUBLIC_BASE_URL is not set — OAuth callbacks will point at localhost "
            "and account linking will fail for real users."
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
    yield


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
    checks["transport"] = TRANSPORT

    ready = checks["database"] == "ok" and checks["encryption"] == "ok"
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"ready": ready, "checks": checks},
    )
