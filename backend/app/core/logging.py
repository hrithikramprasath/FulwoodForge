"""Single-line structured JSON application logger (`REQ-LOG-01`).

Unidirectional Dependency Law: Imports ONLY from `app.core.uuid_v7`, `app.core.config`,
and Python standard library.
Emits single-line JSON log records to `stdout` with microsecond UTC ISO-8601 timestamps
and request/actor correlation context variables.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from app.core.uuid_v7 import generate_uuid7_str

request_id_ctx: ContextVar[str | None] = ContextVar("request_id_ctx", default=None)
user_id_ctx: ContextVar[int | None] = ContextVar("user_id_ctx", default=None)
username_ctx: ContextVar[str | None] = ContextVar("username_ctx", default=None)


def bind_request_context(
    *,
    request_id: str | None = None,
    user_id: int | None = None,
    username: str | None = None,
) -> str:
    """Bind correlation identifiers to the current async context and return the `request_id`."""
    resolved_req_id = request_id or generate_uuid7_str()
    request_id_ctx.set(resolved_req_id)
    user_id_ctx.set(user_id)
    username_ctx.set(username)
    return resolved_req_id


def clear_request_context() -> None:
    """Reset correlation context variables at the end of a request lifecycle."""
    request_id_ctx.set(None)
    user_id_ctx.set(None)
    username_ctx.set(None)


class StructuredJsonFormatter(logging.Formatter):
    """Formats every `LogRecord` as a deterministic single-line JSON object (`REQ-LOG-01`)."""

    def format(self, record: logging.LogRecord) -> str:
        ts_utc = datetime.fromtimestamp(record.created, tz=UTC).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )
        structured_fields: dict[str, Any] = getattr(record, "structured_fields", {})

        payload: dict[str, Any] = {
            "timestamp": ts_utc,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": structured_fields.get("request_id") or request_id_ctx.get(),
            "user_id": (
                structured_fields.get("user_id")
                if "user_id" in structured_fields
                else user_id_ctx.get()
            ),
            "username": structured_fields.get("username") or username_ctx.get(),
            "event": structured_fields.get("event", "LOG"),
            "path": structured_fields.get("path"),
            "method": structured_fields.get("method"),
            "status_code": structured_fields.get("status_code"),
            "duration_ms": structured_fields.get("duration_ms"),
        }

        for key, value in structured_fields.items():
            if key not in payload:
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, separators=(",", ":"), default=str)


def configure_logging(level: int = logging.INFO) -> None:
    """Configure the root logger to emit single-line `StructuredJsonFormatter` records to `stdout`."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredJsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


def log_event(
    logger: logging.Logger,
    event: str,
    message: str,
    *,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    """Emit a structured JSON log entry with an explicit `event` type and metadata fields."""
    merged: dict[str, Any] = {"event": event, **fields}
    logger.log(level, message, extra={"structured_fields": merged})
