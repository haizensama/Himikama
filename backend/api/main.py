"""FastAPI application entry point for the secured Himikama backend."""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.analysis_worker import AnalysisWorker
from api.account_deletion_worker import AccountDeletionWorker
from api.config import config
from api.routes.analysis import router as analysis_router
from api.routes.users import router as users_router


def configure_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI):
    worker: AnalysisWorker | None = None
    deletion_worker: AccountDeletionWorker | None = None
    if config.analysis_worker_enabled:
        worker = AnalysisWorker()
        application.state.analysis_worker = worker
        await worker.start()
    if config.account_deletion_worker_enabled:
        deletion_worker = AccountDeletionWorker()
        application.state.account_deletion_worker = deletion_worker
        await deletion_worker.start()
    try:
        yield
    finally:
        if worker is not None:
            await worker.stop()
        if deletion_worker is not None:
            await deletion_worker.stop()


app = FastAPI(
    title="Himikama API",
    description="Sri Lankan Fundamental Rights legal triage backend.",
    version="0.4.0",
    lifespan=lifespan,
    docs_url=None if config.is_production else "/docs",
    redoc_url=None if config.is_production else "/redoc",
    openapi_url=None if config.is_production else "/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(config.cors_allowed_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Firebase-AppCheck"],
)


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", "").strip()
    if not request_id or len(request_id) > 100:
        request_id = str(uuid.uuid4())
    request.state.request_id = request_id

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > config.max_request_bytes:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Request body is too large"},
                    headers={"X-Request-ID": request_id},
                )
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={"detail": "Invalid Content-Length header"},
                headers={"X-Request-ID": request_id},
            )

    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    safe_errors = [
        {
            "type": error.get("type"),
            "location": list(error.get("loc", ())),
            "message": error.get("msg", "Invalid value"),
        }
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={
            "detail": "The submitted data is invalid",
            # Pydantic error objects include the rejected input by default.
            # Do not echo potentially sensitive legal narratives back here.
            "errors": safe_errors,
            "request_id": getattr(request.state, "request_id", None),
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    logger.exception(
        "Unhandled API error request_id=%s error_type=%s",
        request_id,
        type(exc).__name__,
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": "An internal error occurred",
            "request_id": request_id,
        },
    )


@app.get("/")
async def root() -> dict[str, str]:
    return {"app": "Himikama API", "status": "running"}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(users_router)
app.include_router(analysis_router)
