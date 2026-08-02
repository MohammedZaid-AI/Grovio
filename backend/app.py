from dotenv import load_dotenv
load_dotenv()

from contextlib import asynccontextmanager

from fastapi import FastAPI

import db
import ai.providers
from backend.linking import router as linking_router
from backend.routes import router
from backend.whatsapp_worker import recover_pending


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure the schema exists, wire up the available platforms, then recover
    # anything left in flight by a previous run so a restart never loses a reply.
    db.init_db()
    ai.providers.setup()
    await recover_pending()
    yield


app = FastAPI(title="Food Concierge", lifespan=lifespan)

app.include_router(router)
app.include_router(linking_router)


@app.get("/")
def home():
    return {"status": "running", "service": "AI Food Concierge"}
