from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import os

from backend.routes import router

app = FastAPI(
    title="Grovio API"
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