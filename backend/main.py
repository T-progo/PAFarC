from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from backend import auth, consultations, documents, patients
from backend.config import get_settings
from backend.crypto import get_crypto
from backend.database import init_db

APP_NAME = "PharmaTech"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    get_settings()  # fail fast on missing/invalid configuration
    get_crypto()  # fail fast on missing/invalid encryption keys
    init_db()
    yield


app = FastAPI(
    title=APP_NAME,
    description="PAFarC - Programa de Aperfeiçoamento em Farmácia Clínica",
    version="0.4.0",
    lifespan=lifespan,
)
app.include_router(auth.router)
app.include_router(patients.router)
app.include_router(consultations.router)
app.include_router(documents.router)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    # Same shape as FastAPI's default, without echoing submitted values
    # (CPF, names, passwords) back in error responses.
    errors = [{k: v for k, v in e.items() if k not in ("input", "ctx")} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})


@app.get("/health", tags=["status"])
def health() -> dict[str, str]:
    return {"status": "ok", "app": APP_NAME}
