from dotenv import load_dotenv
load_dotenv()

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import os

import db
from backend.routes import router
from backend.whatsapp_worker import recover_pending


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure schema (incl. the WhatsApp async-delivery tables) exists, then
    # recover any messages/replies left queued by a previous run so a restart
    # never loses an in-flight reply.
    db.init_db()
    await recover_pending()
    yield


app = FastAPI(
    title="Grovio API",
    lifespan=lifespan,
)

# Ensure backend/static directory exists
os.makedirs("backend/static", exist_ok=True)

app.mount("/static", StaticFiles(directory="backend/static"), name="static")

app.include_router(router)


@app.get("/")
def home():

    return {
        "status": "running",
        "service": "Grovio AI COO"
    }