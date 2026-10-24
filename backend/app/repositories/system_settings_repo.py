"""Parameterized MySQL 8.4 repository for `system_settings` (`backend/app/repositories/system_settings_repo.py`).

Unidirectional Dependency Law: Imports ONLY from `app.db`, `app.repositories.base`,
`app.schemas`, and Python standard library.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast

from app.db.errors import classify_mysql_exception
from app.db.pool import AsyncConnectionProtocol
from app.repositories.base import ensure_utc_datetime
from app.schemas.admin import SystemSettingsDTO, SystemSettingsUpdateRequest
from app.schemas.auth import RegistrationMode
from app.schemas.content import (
    ALL_SUBJECTS,
    ALL_TASK_TYPES,
    SubjectLiteral,
    TaskTypeLiteral,
)

_SELECT_SETTINGS_SQL = """
SELECT
    id,
    registration_mode,
    default_hourly_quota,
    maintenance_mode,
    allowed_subjects_json,
    allowed_task_types_json,
    updated_by_user_id,
    updated_at
FROM system_settings
ORDER BY id ASC
LIMIT 1
"""

_INSERT_DEFAULT_SETTINGS_SQL = """
INSERT INTO system_settings (
    id,
    registration_mode,
    default_hourly_quota,
    maintenance_mode,
    allowed_subjects_json,
    allowed_task_types_json,
    updated_by_user_id
) VALUES (1, %s, %s, %s, %s, %s, NULL)
ON DUPLICATE KEY UPDATE id = id
"""

_UPDATE_SETTINGS_SQL = """
UPDATE system_settings
SET
    registration_mode = %s,
    default_hourly_quota = %s,
    maintenance_mode = %s,
    allowed_subjects_json = %s,
    allowed_task_types_json = %s,
    updated_by_user_id = %s,
    updated_at = UTC_TIMESTAMP(6)
WHERE id = %s
"""


def _parse_json_list(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        parsed = json.loads(raw.decode("utf-8"))
        return parsed if isinstance(parsed, list) else []
    if isinstance(raw, str):
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    return []


def _hydrate_settings_row(row: tuple[Any, ...]) -> SystemSettingsDTO:
    _, reg_mode, quota, maint, subjects_raw, tasks_raw, _, updated_at = row
    subjects = [cast(SubjectLiteral, str(s)) for s in _parse_json_list(subjects_raw)]
    tasks = [cast(TaskTypeLiteral, str(t)) for t in _parse_json_list(tasks_raw)]
    return SystemSettingsDTO(
        registration_mode=cast(RegistrationMode, str(reg_mode)),
        default_hourly_quota=int(quota),
        maintenance_mode=bool(maint),
        allowed_subjects=subjects or list(ALL_SUBJECTS),
        allowed_task_types=tasks or list(ALL_TASK_TYPES),
        updated_at=ensure_utc_datetime(updated_at)
        if isinstance(updated_at, datetime)
        else datetime.now(UTC),
    )


class SystemSettingsRepository:
    """Data access methods for the singleton `system_settings` row."""

    async def get_or_init_settings(
        self, conn: AsyncConnectionProtocol
    ) -> SystemSettingsDTO:
        """Fetch the active `system_settings` row or initialize defaults if empty."""
        try:
            async with conn.cursor() as cur:
                await cur.execute(_SELECT_SETTINGS_SQL)
                row = await cur.fetchone()
                if row is not None:
                    return _hydrate_settings_row(row)

                default_subjects = json.dumps(list(ALL_SUBJECTS))
                default_tasks = json.dumps(list(ALL_TASK_TYPES))
                await cur.execute(
                    _INSERT_DEFAULT_SETTINGS_SQL,
                    ("INVITE_ONLY", 30, 0, default_subjects, default_tasks),
                )
                await cur.execute(_SELECT_SETTINGS_SQL)
                created_row = await cur.fetchone()
                if created_row is not None:
                    return _hydrate_settings_row(created_row)

            return SystemSettingsDTO(
                registration_mode="INVITE_ONLY",
                default_hourly_quota=30,
                maintenance_mode=False,
                allowed_subjects=list(ALL_SUBJECTS),
                allowed_task_types=list(ALL_TASK_TYPES),
                updated_at=datetime.now(UTC),
            )
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc

    async def update_settings(
        self,
        conn: AsyncConnectionProtocol,
        update: SystemSettingsUpdateRequest,
        updated_by_user_id: int,
    ) -> SystemSettingsDTO:
        """Apply partial updates to `system_settings` and return the updated DTO."""
        current = await self.get_or_init_settings(conn)
        new_mode = (
            update.registration_mode
            if update.registration_mode is not None
            else current.registration_mode
        )
        new_quota = (
            update.default_hourly_quota
            if update.default_hourly_quota is not None
            else current.default_hourly_quota
        )
        new_maint = (
            update.maintenance_mode
            if update.maintenance_mode is not None
            else current.maintenance_mode
        )
        new_subjects = (
            update.allowed_subjects
            if update.allowed_subjects is not None
            else current.allowed_subjects
        )
        new_tasks = (
            update.allowed_task_types
            if update.allowed_task_types is not None
            else current.allowed_task_types
        )

        try:
            async with conn.cursor() as cur:
                await cur.execute(_SELECT_SETTINGS_SQL)
                existing = await cur.fetchone()
                settings_id = int(existing[0]) if existing is not None else 1

                await cur.execute(
                    _UPDATE_SETTINGS_SQL,
                    (
                        new_mode,
                        int(new_quota),
                        1 if new_maint else 0,
                        json.dumps(list(new_subjects)),
                        json.dumps(list(new_tasks)),
                        int(updated_by_user_id),
                        settings_id,
                    ),
                )
                await cur.execute(_SELECT_SETTINGS_SQL)
                updated_row = await cur.fetchone()
                if updated_row is not None:
                    return _hydrate_settings_row(updated_row)

            return SystemSettingsDTO(
                registration_mode=new_mode,
                default_hourly_quota=new_quota,
                maintenance_mode=new_maint,
                allowed_subjects=list(new_subjects),
                allowed_task_types=list(new_tasks),
                updated_at=current.updated_at,
            )
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc
