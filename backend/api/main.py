"""
himikama/backend/api/main.py
═══════════════════════════════════════════════════════════════
FastAPI application entrypoint for Himikama backend.
═══════════════════════════════════════════════════════════════
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.config import config
from api.routes.analysis import router as analysis_router


def configure_logging() -> None:
    """
    Configure Python logging using LOG_LEVEL from .env.
    """
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


configure_logging()

app = FastAPI(
    title="Himikama API",
    description="Sri Lankan Fundamental Rights legal triage backend.",
    version="0.1.0",
)

# Local development CORS settings.
# Tighten these before production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root() -> dict:
    return {
        "app": "Himikama API",
        "status": "running",
        "model": config.gemini_model,
        "db_path": config.db_path,
    }


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
    }


app.include_router(analysis_router)
