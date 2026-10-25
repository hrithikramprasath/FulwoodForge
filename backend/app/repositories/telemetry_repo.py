"""Parameterized MySQL 8.4 repository for `token_ledger` and `audit_events` (`backend/app/repositories/telemetry_repo.py`).

Unidirectional Dependency Law: Imports ONLY from `app.core.uuid_v7`, `app.db`,
`app.repositories.base`, `app.schemas`, and Python standard library (`REQ-LOG-02`, `REQ-LOG-03`, `REQ-AUTH-03`).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal, cast

from app.core.uuid_v7 import generate_uuid7_bytes, uuid7_bytes_to_str
from app.db.errors import classify_mysql_exception
from app.db.pool import AsyncConnectionProtocol
from app.repositories.base import (
    KeysetCursor,
    KeysetPage,
    ensure_utc_datetime,
    to_mysql_utc,
)
from app.schemas.admin import (
    AdminOverviewMetricsDTO,
    AuditEventDTO,
    TokenLedgerEntryDTO,
)

LedgerStatus = Literal["SUCCESS", "CACHE_HIT", "UPSTREAM_ERROR"]

_FOUR_DP = Decimal("0.0001")

_INSERT_TOKEN_LEDGER_SQL = """
INSERT INTO token_ledger (
    uuid_v7,
    user_id,
    generation_id,
    model_name,
    prompt_tokens,
    candidate_tokens,
    total_tokens,
    estimated_cost_usd,
    latency_ms,
    cache_hit,
    status
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

_INSERT_AUDIT_EVENT_SQL = """
INSERT INTO audit_events (
    uuid_v7,
    actor_user_id,
    target_user_id,
    event_type,
    ip_address,
    metadata_json
) VALUES (%s, %s, %s, %s, %s, %s)
"""

_COUNT_FAILED_LOGINS_SINCE_SQL = """
SELECT COUNT(*)
FROM audit_events
WHERE event_type IN ('AUTH_LOGIN_FAILED', 'LOGIN_FAILED')
  AND ip_address = %s
  AND created_at >= %s
  AND JSON_UNQUOTE(JSON_EXTRACT(metadata_json, '$.username')) = %s
"""

_COUNT_IP_FAILED_LOGINS_SINCE_SQL = """
SELECT COUNT(*)
FROM audit_events
WHERE event_type = 'AUTH_LOGIN_FAILED'
  AND ip_address = %s
  AND created_at >= %s
"""

_LIST_AUDIT_FIRST_SQL = """
SELECT
    a.id,
    a.uuid_v7,
    actor.username AS actor_username,
    target.username AS target_username,
    a.event_type,
    a.ip_address,
    a.metadata_json,
    a.created_at
FROM audit_events a
LEFT JOIN users actor ON actor.id = a.actor_user_id
LEFT JOIN users target ON target.id = a.target_user_id
WHERE (%s IS NULL OR a.event_type = %s)
  AND (%s IS NULL OR a.actor_user_id = %s)
ORDER BY a.created_at DESC, a.id DESC
LIMIT %s
"""

_LIST_AUDIT_CURSOR_SQL = """
SELECT
    a.id,
    a.uuid_v7,
    actor.username AS actor_username,
    target.username AS target_username,
    a.event_type,
    a.ip_address,
    a.metadata_json,
    a.created_at
FROM audit_events a
LEFT JOIN users actor ON actor.id = a.actor_user_id
LEFT JOIN users target ON target.id = a.target_user_id
WHERE (%s IS NULL OR a.event_type = %s)
  AND (%s IS NULL OR a.actor_user_id = %s)
  AND (a.created_at, a.id) < (%s, %s)
ORDER BY a.created_at DESC, a.id DESC
LIMIT %s
"""

_LIST_LEDGER_FIRST_SQL = """
SELECT
    tl.id,
    tl.uuid_v7,
    u.uuid_v7 AS user_uuid,
    u.username,
    g.uuid_v7 AS generation_uuid,
    tl.model_name,
    tl.prompt_tokens,
    tl.candidate_tokens,
    tl.total_tokens,
    tl.estimated_cost_usd,
    tl.latency_ms,
    tl.cache_hit,
    tl.status,
    tl.created_at
FROM token_ledger tl
INNER JOIN users u ON u.id = tl.user_id
LEFT JOIN lesson_generations g ON g.id = tl.generation_id
WHERE (%s IS NULL OR tl.user_id = %s)
ORDER BY tl.created_at DESC, tl.id DESC
LIMIT %s
"""

_LIST_LEDGER_CURSOR_SQL = """
SELECT
    tl.id,
    tl.uuid_v7,
    u.uuid_v7 AS user_uuid,
    u.username,
    g.uuid_v7 AS generation_uuid,
    tl.model_name,
    tl.prompt_tokens,
    tl.candidate_tokens,
    tl.total_tokens,
    tl.estimated_cost_usd,
    tl.latency_ms,
    tl.cache_hit,
    tl.status,
    tl.created_at
FROM token_ledger tl
INNER JOIN users u ON u.id = tl.user_id
LEFT JOIN lesson_generations g ON g.id = tl.generation_id
WHERE (%s IS NULL OR tl.user_id = %s)
  AND (tl.created_at, tl.id) < (%s, %s)
ORDER BY tl.created_at DESC, tl.id DESC
LIMIT %s
"""

_OVERVIEW_METRICS_SQL = """
SELECT
    (SELECT COUNT(*) FROM users) AS total_users,
    (SELECT COUNT(*) FROM users WHERE status = 'ACTIVE') AS active_users,
    (SELECT COUNT(*) FROM users WHERE status = 'PENDING') AS pending_users,
    (SELECT COUNT(*) FROM user_sessions WHERE revoked_at IS NULL AND expires_at > %s) AS active_sessions,
    (SELECT COUNT(*) FROM lesson_generations WHERE created_at >= %s) AS generations_24h,
    (SELECT COUNT(*) FROM token_ledger WHERE created_at >= %s) AS ledger_calls_24h,
    (SELECT COUNT(*) FROM token_ledger WHERE created_at >= %s AND cache_hit = 1) AS cache_hits_24h,
    (SELECT COALESCE(SUM(total_tokens), 0) FROM token_ledger WHERE created_at >= %s) AS total_tokens_24h,
    (SELECT COALESCE(SUM(estimated_cost_usd), 0.0000) FROM token_ledger WHERE created_at >= %s) AS total_cost_usd_24h,
    (SELECT COALESCE(SUM(estimated_cost_usd), 0.0000) FROM token_ledger) AS cumulative_cost_usd
"""


def _parse_metadata_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        parsed = json.loads(raw.decode("utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    if isinstance(raw, str):
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _to_decimal_4dp(val: Any) -> Decimal:
    if isinstance(val, Decimal):
        return val.quantize(_FOUR_DP, rounding=ROUND_HALF_UP)
    return Decimal(str(val or "0.0000")).quantize(_FOUR_DP, rounding=ROUND_HALF_UP)


class TelemetryRepository:
    """Parameterized data access layer for `token_ledger` and `audit_events`."""

    async def insert_token_ledger(
        self,
        conn: AsyncConnectionProtocol,
        *,
        user_id: int,
        generation_id: int | None,
        model_name: str,
        prompt_tokens: int,
        candidate_tokens: int,
        total_tokens: int,
        estimated_cost_usd: Decimal,
        latency_ms: int,
        cache_hit: bool,
        status: LedgerStatus,
        uuid_bytes: bytes | None = None,
    ) -> bytes:
        """Insert a `token_ledger` row with `DECIMAL(19,4)` cost quantization and return `uuid_v7`."""
        resolved_uuid = uuid_bytes or generate_uuid7_bytes()
        quantized_cost = _to_decimal_4dp(estimated_cost_usd)
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    _INSERT_TOKEN_LEDGER_SQL,
                    (
                        resolved_uuid,
                        int(user_id),
                        int(generation_id) if generation_id is not None else None,
                        model_name[:64],
                        max(0, int(prompt_tokens)),
                        max(0, int(candidate_tokens)),
                        max(0, int(total_tokens)),
                        quantized_cost,
                        max(0, int(latency_ms)),
                        1 if cache_hit else 0,
                        status,
                    ),
                )
            return resolved_uuid
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc

    async def insert_audit_event(
        self,
        conn: AsyncConnectionProtocol,
        actor_user_id: int | None,
        target_user_id: int | None,
        event_type: str,
        ip_address: str,
        metadata: Mapping[str, Any],
        *,
        uuid_bytes: bytes | None = None,
    ) -> bytes:
        """Insert an immutable row into `audit_events` (`REQ-LOG-02`) and return `uuid_v7`."""
        resolved_uuid = uuid_bytes or generate_uuid7_bytes()
        serialized_meta = json.dumps(dict(metadata), separators=(",", ":"), default=str)
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    _INSERT_AUDIT_EVENT_SQL,
                    (
                        resolved_uuid,
                        int(actor_user_id) if actor_user_id is not None else None,
                        int(target_user_id) if target_user_id is not None else None,
                        event_type[:64],
                        ip_address[:45],
                        serialized_meta,
                    ),
                )
            return resolved_uuid
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc

    async def count_failed_logins_since(
        self,
        conn: AsyncConnectionProtocol,
        username: str,
        ip_address: str,
        since_utc: datetime,
    ) -> int:
        """Count failed login attempts for `(username, ip_address)` since `since_utc` (`REQ-AUTH-03`)."""
        normalized_username = username.strip().lower()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    _COUNT_FAILED_LOGINS_SINCE_SQL,
                    (
                        ip_address[:45],
                        to_mysql_utc(since_utc),
                        normalized_username,
                    ),
                )
                row = await cur.fetchone()
                return int(row[0]) if row is not None else 0
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc

    async def count_ip_failed_logins_since(
        self,
        conn: AsyncConnectionProtocol,
        *,
        ip_address: str,
        since_utc: datetime,
    ) -> int:
        """Count failed login attempts from `ip_address` across all usernames since `since_utc`."""
        since_str = to_mysql_utc(since_utc)
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    _COUNT_IP_FAILED_LOGINS_SINCE_SQL,
                    (ip_address[:45], since_str),
                )
                row = await cur.fetchone()
                return int(row[0]) if row and row[0] is not None else 0
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc

    async def list_audit_events_keyset(
        self,
        conn: AsyncConnectionProtocol,
        cursor: KeysetCursor | None = None,
        limit: int = 50,
        event_type: str | None = None,
        actor_user_id: int | None = None,
    ) -> KeysetPage[AuditEventDTO]:
        """List audit events joined with actor/target usernames using composite keyset pagination."""
        clamped_limit = max(1, min(int(limit), 200))
        fetch_limit = clamped_limit + 1
        try:
            async with conn.cursor() as cur:
                if cursor is None:
                    await cur.execute(
                        _LIST_AUDIT_FIRST_SQL,
                        (
                            event_type,
                            event_type,
                            actor_user_id,
                            actor_user_id,
                            fetch_limit,
                        ),
                    )
                else:
                    await cur.execute(
                        _LIST_AUDIT_CURSOR_SQL,
                        (
                            event_type,
                            event_type,
                            actor_user_id,
                            actor_user_id,
                            to_mysql_utc(cursor.created_at),
                            int(cursor.id),
                            fetch_limit,
                        ),
                    )
                raw_rows = await cur.fetchall()

            has_more = len(raw_rows) > clamped_limit
            trimmed = raw_rows[:clamped_limit]
            dtos: list[AuditEventDTO] = []
            last_created: datetime | None = None
            last_id: int | None = None

            for row in trimmed:
                (
                    row_id,
                    uuid_raw,
                    actor_username,
                    target_username,
                    ev_type,
                    ip_addr,
                    meta_raw,
                    created_at,
                ) = row
                utc_created = ensure_utc_datetime(created_at)
                last_created = utc_created
                last_id = int(row_id)
                dtos.append(
                    AuditEventDTO(
                        uuid=uuid7_bytes_to_str(bytes(uuid_raw)),
                        actor_username=str(actor_username)
                        if actor_username is not None
                        else None,
                        target_username=str(target_username)
                        if target_username is not None
                        else None,
                        event_type=str(ev_type),
                        ip_address=str(ip_addr),
                        metadata=_parse_metadata_dict(meta_raw),
                        created_at=utc_created,
                    )
                )

            next_cursor = (
                KeysetCursor(created_at=last_created, id=last_id).encode()
                if has_more and last_created is not None and last_id is not None
                else None
            )
            return KeysetPage(items=dtos, next_cursor=next_cursor)
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc

    async def list_token_ledger_keyset(
        self,
        conn: AsyncConnectionProtocol,
        cursor: KeysetCursor | None = None,
        limit: int = 50,
        user_id: int | None = None,
    ) -> KeysetPage[TokenLedgerEntryDTO]:
        """List token ledger rows joined with user/generation UUIDs using composite keyset pagination."""
        clamped_limit = max(1, min(int(limit), 200))
        fetch_limit = clamped_limit + 1
        try:
            async with conn.cursor() as cur:
                if cursor is None:
                    await cur.execute(
                        _LIST_LEDGER_FIRST_SQL,
                        (user_id, user_id, fetch_limit),
                    )
                else:
                    await cur.execute(
                        _LIST_LEDGER_CURSOR_SQL,
                        (
                            user_id,
                            user_id,
                            to_mysql_utc(cursor.created_at),
                            int(cursor.id),
                            fetch_limit,
                        ),
                    )
                raw_rows = await cur.fetchall()

            has_more = len(raw_rows) > clamped_limit
            trimmed = raw_rows[:clamped_limit]
            dtos: list[TokenLedgerEntryDTO] = []
            last_created: datetime | None = None
            last_id: int | None = None

            for row in trimmed:
                (
                    row_id,
                    uuid_raw,
                    user_uuid_raw,
                    username,
                    gen_uuid_raw,
                    model_name,
                    prompt_tokens,
                    candidate_tokens,
                    total_tokens,
                    estimated_cost_usd,
                    latency_ms,
                    cache_hit,
                    status,
                    created_at,
                ) = row
                utc_created = ensure_utc_datetime(created_at)
                last_created = utc_created
                last_id = int(row_id)
                dtos.append(
                    TokenLedgerEntryDTO(
                        uuid=uuid7_bytes_to_str(bytes(uuid_raw)),
                        user_uuid=uuid7_bytes_to_str(bytes(user_uuid_raw)),
                        username=str(username),
                        generation_uuid=(
                            uuid7_bytes_to_str(bytes(gen_uuid_raw))
                            if gen_uuid_raw is not None
                            else None
                        ),
                        model_name=str(model_name),
                        prompt_tokens=int(prompt_tokens),
                        candidate_tokens=int(candidate_tokens),
                        total_tokens=int(total_tokens),
                        estimated_cost_usd=_to_decimal_4dp(estimated_cost_usd),
                        latency_ms=int(latency_ms),
                        cache_hit=bool(cache_hit),
                        status=cast(LedgerStatus, str(status)),
                        created_at=utc_created,
                    )
                )

            next_cursor = (
                KeysetCursor(created_at=last_created, id=last_id).encode()
                if has_more and last_created is not None and last_id is not None
                else None
            )
            return KeysetPage(items=dtos, next_cursor=next_cursor)
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc

    async def get_admin_overview_metrics(
        self,
        conn: AsyncConnectionProtocol,
        now_utc: datetime,
    ) -> AdminOverviewMetricsDTO:
        """Compute `/admin` overview telemetry metrics (`REQ-ADMIN-02`)."""
        mysql_now = to_mysql_utc(now_utc)
        mysql_since_24h = to_mysql_utc(now_utc - timedelta(hours=24))
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    _OVERVIEW_METRICS_SQL,
                    (
                        mysql_now,
                        mysql_since_24h,
                        mysql_since_24h,
                        mysql_since_24h,
                        mysql_since_24h,
                        mysql_since_24h,
                    ),
                )
                row = await cur.fetchone()
                if row is None:
                    return AdminOverviewMetricsDTO(
                        total_users=0,
                        active_users=0,
                        pending_users=0,
                        active_sessions=0,
                        generations_24h=0,
                        cache_hit_rate_24h=Decimal("0.0000"),
                        total_tokens_24h=0,
                        total_cost_usd_24h=Decimal("0.0000"),
                        cumulative_cost_usd=Decimal("0.0000"),
                    )

                (
                    total_users,
                    active_users,
                    pending_users,
                    active_sessions,
                    generations_24h,
                    ledger_calls_24h,
                    cache_hits_24h,
                    total_tokens_24h,
                    total_cost_usd_24h,
                    cumulative_cost_usd,
                ) = row

                calls_24h_int = int(ledger_calls_24h or 0)
                hits_24h_int = int(cache_hits_24h or 0)
                hit_rate = (
                    (Decimal(hits_24h_int) / Decimal(calls_24h_int)).quantize(
                        _FOUR_DP, rounding=ROUND_HALF_UP
                    )
                    if calls_24h_int > 0
                    else Decimal("0.0000")
                )

                return AdminOverviewMetricsDTO(
                    total_users=int(total_users or 0),
                    active_users=int(active_users or 0),
                    pending_users=int(pending_users or 0),
                    active_sessions=int(active_sessions or 0),
                    generations_24h=int(generations_24h or 0),
                    cache_hit_rate_24h=hit_rate,
                    total_tokens_24h=int(total_tokens_24h or 0),
                    total_cost_usd_24h=_to_decimal_4dp(total_cost_usd_24h),
                    cumulative_cost_usd=_to_decimal_4dp(cumulative_cost_usd),
                )
        except Exception as exc:
            raise classify_mysql_exception(exc) from exc
