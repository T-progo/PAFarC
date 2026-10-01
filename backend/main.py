from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend import auth
from backend.config import get_settings
from backend.database import init_db

APP_NAME = "PharmaTech"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    get_settings()  # fail fast on missing/invalid configuration
    init_db()
    yield


app = FastAPI(
    title=APP_NAME,
    description="PAFarC - Programa de Aperfeiçoamento em Farmácia Clínica",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(auth.router)


@app.get("/health", tags=["status"])
def health() -> dict[str, str]:
    return {"status": "ok", "app": APP_NAME}
