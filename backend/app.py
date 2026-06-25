from fastapi import FastAPI

from backend.routes import router

app = FastAPI(
    title="Grovio API"
)

app.include_router(router)


@app.get("/")
def home():

    return {
        "status": "running",
        "service": "Grovio AI COO"
    }