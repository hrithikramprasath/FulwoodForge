"""Centralized fail-closed application configuration (`backend/app/core/config.py`).

Unidirectional Dependency Law: Imports ONLY from `app.schemas` and Python standard library.
Defaults to `app_env="production"` (fail-closed) so missing credentials, weak admin passwords,
or insecure cookie settings raise `RuntimeError` immediately at startup.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.schemas.auth import USERNAME_PATTERN

AppEnv = Literal["development", "test", "production"]

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"0", "false", "no", "off"})


def _parse_bool(raw: str | None, *, default: bool, name: str) -> bool:
    if raw is None or not raw.strip():
        return default
    cleaned = raw.strip().lower()
    if cleaned in _TRUTHY:
        return True
    if cleaned in _FALSY:
        return False
    raise RuntimeError(f"Invalid boolean value for {name}: {raw!r}")


def _parse_int(
    raw: str | None, *, default: int, minimum: int, maximum: int, name: str
) -> int:
    if raw is None or not raw.strip():
        return default
    cleaned = raw.strip()
    try:
        val = int(cleaned)
    except ValueError as exc:
        raise RuntimeError(f"Invalid integer value for {name}: {raw!r}") from exc
    if not (minimum <= val <= maximum):
        raise RuntimeError(
            f"{name} must be between {minimum} and {maximum} (got {val})"
        )
    return val


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Immutable runtime configuration validated at startup."""

    app_env: AppEnv
    mysql_host: str
    mysql_port: int
    mysql_user: str
    mysql_password: str
    mysql_database: str
    mysql_pool_min: int
    mysql_pool_max: int
    gemini_api_key: str
    gemini_model: str
    session_cookie_name: str
    session_cookie_secure: bool
    session_ttl_hours: int
    bootstrap_admin_username: str
    bootstrap_admin_password: str
    artifacts_dir: Path
    cors_origins: tuple[str, ...] = (
        "http://localhost:8000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    )


def load_config(env: Mapping[str, str] | None = None) -> AppConfig:
    """Load and validate `AppConfig` from an environment mapping or `os.environ`."""
    source: Mapping[str, str] = os.environ if env is None else env

    raw_env = source.get("APP_ENV", "production").strip().lower() or "production"
    if raw_env not in ("development", "test", "production"):
        raise RuntimeError(
            f"APP_ENV must be 'development', 'test', or 'production' (got {raw_env!r})"
        )
    app_env: AppEnv = (
        "development"
        if raw_env == "development"
        else ("test" if raw_env == "test" else "production")
    )

    mysql_host = source.get("MYSQL_HOST", "mysql").strip() or "mysql"
    mysql_port = _parse_int(
        source.get("MYSQL_PORT"),
        default=3306,
        minimum=1,
        maximum=65535,
        name="MYSQL_PORT",
    )
    mysql_user = source.get("MYSQL_USER", "fulwood_app").strip() or "fulwood_app"
    mysql_password = source.get("MYSQL_PASSWORD", "")
    mysql_database = (
        source.get("MYSQL_DATABASE", "fulwood_forge").strip() or "fulwood_forge"
    )
    mysql_pool_min = _parse_int(
        source.get("MYSQL_POOL_MIN"),
        default=2,
        minimum=1,
        maximum=50,
        name="MYSQL_POOL_MIN",
    )
    mysql_pool_max = _parse_int(
        source.get("MYSQL_POOL_MAX"),
        default=10,
        minimum=mysql_pool_min,
        maximum=100,
        name="MYSQL_POOL_MAX",
    )

    gemini_api_key = source.get("GEMINI_API_KEY", "").strip()
    gemini_model = (
        source.get("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()
        or "gemini-3.5-flash-lite"
    )

    session_cookie_name = (
        source.get("SESSION_COOKIE_NAME", "ff_session").strip() or "ff_session"
    )
    default_secure = app_env == "production"
    session_cookie_secure = _parse_bool(
        source.get("SESSION_COOKIE_SECURE"),
        default=default_secure,
        name="SESSION_COOKIE_SECURE",
    )
    session_ttl_hours = _parse_int(
        source.get("SESSION_TTL_HOURS"),
        default=24,
        minimum=1,
        maximum=720,
        name="SESSION_TTL_HOURS",
    )

    raw_admin_user = source.get("BOOTSTRAP_ADMIN_USERNAME", "admin").strip() or "admin"
    if not USERNAME_PATTERN.match(raw_admin_user):
        raise RuntimeError(
            "BOOTSTRAP_ADMIN_USERNAME must match ^[a-zA-Z0-9_.-]{3,32}$ and cannot be an email address"
        )
    bootstrap_admin_username = raw_admin_user.lower()
    bootstrap_admin_password = source.get("BOOTSTRAP_ADMIN_PASSWORD", "")

    default_artifacts = str(Path(tempfile.gettempdir()) / "artifacts")
    artifacts_dir_str = (
        source.get("ARTIFACTS_DIR", default_artifacts).strip() or default_artifacts
    )
    artifacts_dir = Path(artifacts_dir_str)

    raw_cors = source.get("CORS_ORIGINS", "")
    parsed_cors = tuple(o.strip() for o in raw_cors.split(",") if o.strip())
    cors_origins = parsed_cors or (
        "http://localhost:8000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    )

    if app_env == "production":
        if not mysql_password.strip():
            raise RuntimeError(
                "MYSQL_PASSWORD is required when APP_ENV='production' (fail-closed)"
            )
        if not gemini_api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is required when APP_ENV='production' (fail-closed)"
            )
        if not session_cookie_secure:
            raise RuntimeError(
                "SESSION_COOKIE_SECURE must be True when APP_ENV='production' (fail-closed)"
            )
        if len(bootstrap_admin_password) < 12:
            raise RuntimeError(
                "BOOTSTRAP_ADMIN_PASSWORD must be at least 12 characters when APP_ENV='production'"
            )

    return AppConfig(
        app_env=app_env,
        mysql_host=mysql_host,
        mysql_port=mysql_port,
        mysql_user=mysql_user,
        mysql_password=mysql_password,
        mysql_database=mysql_database,
        mysql_pool_min=mysql_pool_min,
        mysql_pool_max=mysql_pool_max,
        gemini_api_key=gemini_api_key,
        gemini_model=gemini_model,
        session_cookie_name=session_cookie_name,
        session_cookie_secure=session_cookie_secure,
        session_ttl_hours=session_ttl_hours,
        bootstrap_admin_username=bootstrap_admin_username,
        bootstrap_admin_password=bootstrap_admin_password,
        artifacts_dir=artifacts_dir,
        cors_origins=cors_origins,
    )
