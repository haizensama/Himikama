"""
himikama/backend/api/config.py
═══════════════════════════════════════════════════════════════
Phase 2 — Application Configuration

Responsibility:
    Load and validate all environment variables from .env.
    Provide a single Config object imported by any module
    that needs configuration values.

    This is the ONLY place environment variables are read.
    No other module calls os.getenv() directly.

Environment Variables (.env):
    GEMINI_API_KEY                  Required. Google Gemini API key.
    GEMINI_MODEL                    Optional. Default: gemini-2.5-flash
    DB_PATH                         Optional. ChromaDB path. Default: db/
    LOG_LEVEL                       Optional. Default: INFO

    FIREBASE_SERVICE_ACCOUNT_PATH   Optional. Path to Firebase service
                                    account JSON. Required when using
                                    Firestore persistence.

    FIRESTORE_USERS_COLLECTION      Optional. Default: users

Legacy compatibility:
    FIREBASE_CREDENTIALS            Older env name for Firebase service
                                    account JSON path. Still supported.

Usage:
    from api.config import config

    key = config.gemini_api_key
    db  = config.db_path
═══════════════════════════════════════════════════════════════
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Config:
    """
    Immutable application configuration.
    Loaded once at import time from environment variables.

    Attributes:
        gemini_api_key:                  Google Gemini API key.
        gemini_model:                    Gemini model name.
        db_path:                         ChromaDB persistent storage path.
        log_level:                       Python logging level string.
        firebase_credentials:            Legacy Firebase credentials path.
        firebase_service_account_path:   Firebase service account JSON path.
        firestore_users_collection:      Firestore top-level users collection.
    """

    gemini_api_key: str
    gemini_model: str
    db_path: str
    log_level: str

    # Legacy field kept so older code does not break
    firebase_credentials: str | None

    # New Firebase / Firestore fields
    firebase_service_account_path: str
    firestore_users_collection: str


def _load_config() -> Config:
    """
    Load configuration from environment variables.
    Called once at module import time.

    Raises:
        RuntimeError: If GEMINI_API_KEY is not set.
    """

    # ─────────────────────────────────────────────────────────
    # Gemini
    # ─────────────────────────────────────────────────────────

    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()

    if not gemini_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set in environment.\n"
            "Add it to himikama/backend/.env:\n"
            "  GEMINI_API_KEY=your_key_here"
        )

    gemini_model = os.getenv(
        "GEMINI_MODEL",
        "gemini-2.5-flash",
    ).strip()

    # ─────────────────────────────────────────────────────────
    # ChromaDB
    # ─────────────────────────────────────────────────────────

    db_path = os.getenv("DB_PATH", "db/").strip()

    if not Path(db_path).exists():
        logger.warning(
            "DB_PATH '%s' does not exist. "
            "Run the ingestion pipeline first.",
            db_path,
        )

    # ─────────────────────────────────────────────────────────
    # Logging
    # ─────────────────────────────────────────────────────────

    log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()

    valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

    if log_level not in valid_levels:
        logger.warning(
            "Invalid LOG_LEVEL '%s'. Defaulting to INFO.",
            log_level,
        )
        log_level = "INFO"

    # ─────────────────────────────────────────────────────────
    # Firebase / Firestore
    # ─────────────────────────────────────────────────────────
    # New preferred env name:
    #     FIREBASE_SERVICE_ACCOUNT_PATH
    #
    # Legacy env name still supported:
    #     FIREBASE_CREDENTIALS
    #
    # If both exist, FIREBASE_SERVICE_ACCOUNT_PATH wins.
    # ─────────────────────────────────────────────────────────

    legacy_firebase_creds = os.getenv("FIREBASE_CREDENTIALS", "").strip()
    legacy_firebase_creds = (
        legacy_firebase_creds
        if legacy_firebase_creds
        else None
    )

    firebase_service_account_path = os.getenv(
        "FIREBASE_SERVICE_ACCOUNT_PATH",
        "",
    ).strip()

    if not firebase_service_account_path:
        firebase_service_account_path = (
            legacy_firebase_creds
            or "secrets/firebase-service-account.json"
        )

    firestore_users_collection = os.getenv(
        "FIRESTORE_USERS_COLLECTION",
        "users",
    ).strip()

    if not firestore_users_collection:
        firestore_users_collection = "users"

    # Do not raise an error here if Firebase file is missing.
    # Some local dev flows may not use Firestore yet.
    # api/firebase.py will raise a clear error only when Firestore is used.
    if firebase_service_account_path and not Path(
        firebase_service_account_path
    ).exists():
        logger.warning(
            "Firebase service account file '%s' does not exist yet. "
            "Firestore endpoints will fail until this file is added.",
            firebase_service_account_path,
        )

    return Config(
        gemini_api_key=gemini_key,
        gemini_model=gemini_model,
        db_path=db_path,
        log_level=log_level,
        firebase_credentials=legacy_firebase_creds,
        firebase_service_account_path=firebase_service_account_path,
        firestore_users_collection=firestore_users_collection,
    )


# Single shared config instance
# Imported by all modules that need configuration
config = _load_config()
