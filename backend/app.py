from dotenv import load_dotenv
load_dotenv()

from contextlib import asynccontextmanager

from fastapi import FastAPI

import db
from backend.routes import router
from backend.whatsapp_worker import recover_pending


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure the delivery-queue schema exists, then recover anything left in
    # flight by a previous run so a restart never loses a reply.
    db.init_db()
    await recover_pending()
    yield


app = FastAPI(title="Food Concierge", lifespan=lifespan)

app.include_router(router)


@app.get("/")
def home():
    return {"status": "running", "service": "AI Food Concierge"}
