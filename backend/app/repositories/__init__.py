"""MySQL 8.4 LTS async repository layer (`backend/app/repositories/`).

Unidirectional Dependency Law: Imports ONLY from `app.core`, `app.db`, `app.schemas`,
and Python standard library.
"""

from __future__ import annotations

from app.repositories.artifacts_repo import ArtifactRow, ArtifactsRepository
from app.repositories.base import (
    KeysetCursor,
    KeysetPage,
    decode_keyset_cursor,
    encode_keyset_cursor,
    ensure_utc_datetime,
    to_mysql_utc,
)
from app.repositories.generations_repo import GenerationRow, GenerationsRepository
from app.repositories.idempotency_repo import (
    IdempotencyRecord,
    IdempotencyRepository,
    IdempotencyStatus,
)
from app.repositories.invite_codes_repo import InviteCodeRow, InviteCodesRepository
from app.repositories.sessions_repo import SessionRow, SessionsRepository
from app.repositories.system_settings_repo import SystemSettingsRepository
from app.repositories.telemetry_repo import LedgerStatus, TelemetryRepository
from app.repositories.users_repo import UserRow, UsersRepository, hydrate_user_row

__all__ = [
    "ArtifactRow",
    "ArtifactsRepository",
    "GenerationRow",
    "GenerationsRepository",
    "IdempotencyRecord",
    "IdempotencyRepository",
    "IdempotencyStatus",
    "InviteCodeRow",
    "InviteCodesRepository",
    "KeysetCursor",
    "KeysetPage",
    "LedgerStatus",
    "SessionRow",
    "SessionsRepository",
    "SystemSettingsRepository",
    "TelemetryRepository",
    "UserRow",
    "UsersRepository",
    "decode_keyset_cursor",
    "encode_keyset_cursor",
    "ensure_utc_datetime",
    "hydrate_user_row",
    "to_mysql_utc",
]
