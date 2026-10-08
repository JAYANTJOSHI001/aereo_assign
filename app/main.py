"""FastAPI application initialization, exception handlers, and router registration."""

from contextlib import asynccontextmanager
import logging
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.certificates import router as certificates_router
from app.api.jobs import router as jobs_router
from app.config import settings
from app.db import Base, engine
from app.workers import shutdown_workers

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
)
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager to handle startup recovery and graceful shutdown."""
    # Create tables on startup
    Base.metadata.create_all(bind=engine)
    # Recover any incomplete/interrupted jobs from previous runs
    from app.services.job_service import recover_incomplete_jobs
    recover_incomplete_jobs()
    yield
    # Graceful shutdown of background workers
    shutdown_workers(wait=True)


app = FastAPI(
    title="Bulk Certificate Generator",
    description="Bulk PDF certificate generator backend with asynchronous progress tracking",
    version="1.0.0",
    lifespan=lifespan,
)


# Global Exception Handlers ensuring uniform {"error": {"code": ..., "message": ...}} shape
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handle 422 schema validation errors uniformly."""
    first_err = exc.errors()[0] if exc.errors() else {}
    msg = first_err.get("msg", "Validation error")
    loc = " -> ".join(str(p) for p in first_err.get("loc", []))
    summary_message = f"{loc}: {msg}" if loc else msg

    clean_details = []
    for err in exc.errors():
        item = {
            "type": err.get("type"),
            "loc": err.get("loc"),
            "msg": err.get("msg"),
        }
        if "input" in err and isinstance(err["input"], (str, int, float, bool, list, dict, type(None))):
            item["input"] = err["input"]
        if "ctx" in err and isinstance(err["ctx"], dict):
            item["ctx"] = {k: str(v) for k, v in err["ctx"].items()}
        clean_details.append(item)

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": summary_message,
                "details": clean_details,
            }
        },
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Handle HTTP exceptions conforming to error response structure."""
    code = "ERROR"
    message = str(exc.detail)

    if isinstance(exc.detail, dict) and "code" in exc.detail:
        code = exc.detail.get("code", "ERROR")
        message = exc.detail.get("message", message)
    elif exc.status_code == 404:
        code = "NOT_FOUND"
    elif exc.status_code == 409:
        code = "CONFLICT"

    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": code, "message": message}},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch unhandled internal errors safely."""
    logger.exception("Unhandled server exception: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "An unexpected internal server error occurred",
            }
        },
    )


# Mount API routers
app.include_router(jobs_router)
app.include_router(certificates_router)


@app.get("/health", summary="Health check")
def health_check() -> dict[str, str]:
    """Liveness probe endpoint."""
    return {"status": "ok"}
