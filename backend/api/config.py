"""Secure, centralized configuration for the Himikama API."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BACKEND_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(BACKEND_ROOT / ".env")

logger = logging.getLogger(__name__)


def _read_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default

    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False

    raise RuntimeError(f"{name} must be true or false")


def _read_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc

    if not minimum <= value <= maximum:
        raise RuntimeError(
            f"{name} must be between {minimum} and {maximum}"
        )
    return value


def _read_csv(name: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None:
        return default
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _resolve_backend_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    return path if path.is_absolute() else (BACKEND_ROOT / path).resolve()


@dataclass(frozen=True)
class Config:
    environment: str
    gemini_api_key: str
    gemini_model: str
    db_path: str
    log_level: str

    firebase_service_account_path: str | None
    firebase_project_id: str | None
    firestore_users_collection: str
    firestore_jobs_collection: str
    firebase_check_revoked_tokens: bool
    firebase_require_verified_email: bool
    firebase_require_app_check: bool
    firebase_app_check_allowed_app_ids: tuple[str, ...]

    analysis_worker_enabled: bool
    analysis_worker_poll_seconds: int
    analysis_job_lease_seconds: int
    analysis_job_heartbeat_seconds: int
    analysis_job_scan_limit: int

    account_deletion_worker_enabled: bool
    account_deletion_worker_poll_seconds: int
    account_deletion_lease_seconds: int
    account_deletion_scan_limit: int
    account_deletion_recovery_days: int

    cors_allowed_origins: tuple[str, ...]
    max_request_bytes: int
    history_max_limit: int
    recent_auth_max_age_seconds: int
    current_terms_version: str
    current_privacy_version: str
    current_assessment_consent_version: str

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


def _load_config() -> Config:
    environment = os.getenv("ENVIRONMENT", "development").strip().lower()
    if environment not in {"development", "test", "production"}:
        raise RuntimeError(
            "ENVIRONMENT must be development, test, or production"
        )

    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not gemini_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Add it to backend/.env."
        )

    gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
    if not gemini_model:
        raise RuntimeError("GEMINI_MODEL cannot be empty")

    db_path = _resolve_backend_path(os.getenv("DB_PATH", "db/").strip())
    if not db_path.exists():
        logger.warning("Configured ChromaDB directory does not exist")

    log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise RuntimeError("LOG_LEVEL contains an unsupported value")

    service_account_raw = (
        os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH", "").strip()
        or os.getenv("FIREBASE_CREDENTIALS", "").strip()
    )
    if service_account_raw:
        service_account_path: str | None = str(
            _resolve_backend_path(service_account_raw)
        )
    else:
        local_default = BACKEND_ROOT / "secrets" / "firebase-service-account.json"
        service_account_path = str(local_default) if local_default.exists() else None

    firebase_project_id = os.getenv("FIREBASE_PROJECT_ID", "").strip() or None
    users_collection = os.getenv("FIRESTORE_USERS_COLLECTION", "users").strip()
    jobs_collection = os.getenv(
        "FIRESTORE_ANALYSIS_JOBS_COLLECTION", "analysis_jobs"
    ).strip()
    for variable, collection in (
        ("FIRESTORE_USERS_COLLECTION", users_collection),
        ("FIRESTORE_ANALYSIS_JOBS_COLLECTION", jobs_collection),
    ):
        if not collection or "/" in collection:
            raise RuntimeError(f"{variable} must be one collection name")

    check_revoked_tokens = _read_bool("FIREBASE_CHECK_REVOKED_TOKENS", True)
    require_verified_email = _read_bool(
        "FIREBASE_REQUIRE_VERIFIED_EMAIL", True
    )
    require_app_check = _read_bool("FIREBASE_REQUIRE_APP_CHECK", False)
    app_check_allowed_app_ids = _read_csv(
        "FIREBASE_APP_CHECK_ALLOWED_APP_IDS"
    )
    for app_id in app_check_allowed_app_ids:
        if (
            len(app_id) > 256
            or any(character.isspace() for character in app_id)
            or app_id.count(":") < 3
        ):
            raise RuntimeError(
                "FIREBASE_APP_CHECK_ALLOWED_APP_IDS contains an invalid "
                "Firebase App ID"
            )

    worker_enabled = _read_bool(
        "ANALYSIS_WORKER_ENABLED", environment != "test"
    )
    worker_poll_seconds = _read_int(
        "ANALYSIS_WORKER_POLL_SECONDS", 2, minimum=1, maximum=60
    )
    job_lease_seconds = _read_int(
        "ANALYSIS_JOB_LEASE_SECONDS", 120, minimum=30, maximum=3_600
    )
    job_heartbeat_seconds = _read_int(
        "ANALYSIS_JOB_HEARTBEAT_SECONDS", 30, minimum=5, maximum=1_200
    )
    if job_heartbeat_seconds * 2 >= job_lease_seconds:
        raise RuntimeError(
            "ANALYSIS_JOB_HEARTBEAT_SECONDS must be less than half of "
            "ANALYSIS_JOB_LEASE_SECONDS"
        )

    deletion_worker_enabled = _read_bool(
        "ACCOUNT_DELETION_WORKER_ENABLED", environment != "test"
    )

    if environment == "production":
        missing_hardening: list[str] = []
        if not firebase_project_id:
            missing_hardening.append("FIREBASE_PROJECT_ID")
        if not check_revoked_tokens:
            missing_hardening.append("FIREBASE_CHECK_REVOKED_TOKENS=true")
        if not require_verified_email:
            missing_hardening.append("FIREBASE_REQUIRE_VERIFIED_EMAIL=true")
        if not require_app_check:
            missing_hardening.append("FIREBASE_REQUIRE_APP_CHECK=true")
        if not app_check_allowed_app_ids:
            missing_hardening.append("FIREBASE_APP_CHECK_ALLOWED_APP_IDS")
        if not worker_enabled:
            missing_hardening.append("ANALYSIS_WORKER_ENABLED=true")
        if not deletion_worker_enabled:
            missing_hardening.append("ACCOUNT_DELETION_WORKER_ENABLED=true")
        if missing_hardening:
            raise RuntimeError(
                "Production security configuration is incomplete: "
                + ", ".join(missing_hardening)
            )

    development_origins = (
        "http://localhost",
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    )
    cors_origins = _read_csv(
        "CORS_ALLOWED_ORIGINS",
        () if environment == "production" else development_origins,
    )

    return Config(
        environment=environment,
        gemini_api_key=gemini_key,
        gemini_model=gemini_model,
        db_path=str(db_path),
        log_level=log_level,
        firebase_service_account_path=service_account_path,
        firebase_project_id=firebase_project_id,
        firestore_users_collection=users_collection,
        firestore_jobs_collection=jobs_collection,
        firebase_check_revoked_tokens=check_revoked_tokens,
        firebase_require_verified_email=require_verified_email,
        firebase_require_app_check=require_app_check,
        firebase_app_check_allowed_app_ids=app_check_allowed_app_ids,
        analysis_worker_enabled=worker_enabled,
        analysis_worker_poll_seconds=worker_poll_seconds,
        analysis_job_lease_seconds=job_lease_seconds,
        analysis_job_heartbeat_seconds=job_heartbeat_seconds,
        analysis_job_scan_limit=_read_int(
            "ANALYSIS_JOB_SCAN_LIMIT", 25, minimum=1, maximum=100
        ),
        account_deletion_worker_enabled=deletion_worker_enabled,
        account_deletion_worker_poll_seconds=_read_int(
            "ACCOUNT_DELETION_WORKER_POLL_SECONDS",
            60,
            minimum=5,
            maximum=3_600,
        ),
        account_deletion_lease_seconds=_read_int(
            "ACCOUNT_DELETION_LEASE_SECONDS",
            300,
            minimum=60,
            maximum=3_600,
        ),
        account_deletion_scan_limit=_read_int(
            "ACCOUNT_DELETION_SCAN_LIMIT", 25, minimum=1, maximum=100
        ),
        account_deletion_recovery_days=_read_int(
            "ACCOUNT_DELETION_RECOVERY_DAYS", 7, minimum=1, maximum=30
        ),
        cors_allowed_origins=cors_origins,
        max_request_bytes=_read_int(
            "MAX_REQUEST_BYTES", 65_536, minimum=4_096, maximum=1_048_576
        ),
        history_max_limit=_read_int(
            "HISTORY_MAX_LIMIT", 50, minimum=1, maximum=100
        ),
        recent_auth_max_age_seconds=_read_int(
            "RECENT_AUTH_MAX_AGE_SECONDS", 600, minimum=60, maximum=3_600
        ),
        current_terms_version=os.getenv(
            "CURRENT_TERMS_VERSION", "1.0"
        ).strip(),
        current_privacy_version=os.getenv(
            "CURRENT_PRIVACY_VERSION", "1.1"
        ).strip(),
        current_assessment_consent_version=os.getenv(
            "CURRENT_ASSESSMENT_CONSENT_VERSION", "1.0"
        ).strip(),
    )


config = _load_config()
