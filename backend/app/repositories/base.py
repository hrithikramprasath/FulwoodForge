"""Shared keyset pagination types and UTC datetime helpers (`backend/app/repositories/base.py`).

Unidirectional Dependency Law: Imports ONLY from Python standard library.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Generic, TypeVar

T = TypeVar("T")


def ensure_utc_datetime(dt: datetime) -> datetime:
    """Attach or convert a `datetime` to `UTC` timezone."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def to_mysql_utc(dt: datetime) -> datetime:
    """Convert a `datetime` to naive UTC `DATETIME(6)` for MySQL parameter binding."""
    return ensure_utc_datetime(dt).replace(tzinfo=None)


@dataclass(frozen=True, slots=True)
class KeysetCursor:
    """Composite `(created_at, id)` keyset cursor for $O(\\log N)$ B+ Tree pagination."""

    created_at: datetime
    id: int

    def encode(self) -> str:
        """Encode this cursor as an opaque URL-safe base64 token."""
        return encode_keyset_cursor(self)

    @classmethod
    def decode(cls, token: str) -> KeysetCursor:
        """Decode an opaque URL-safe base64 token into a `KeysetCursor`."""
        return decode_keyset_cursor(token)


@dataclass(frozen=True, slots=True)
class KeysetPage(Generic[T]):
    """A single keyset-paginated page of domain rows or DTOs."""

    items: list[T]
    next_cursor: str | None


def encode_keyset_cursor(cursor: KeysetCursor) -> str:
    """Serialize a `KeysetCursor` into a compact base64url string."""
    ts_utc = ensure_utc_datetime(cursor.created_at).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    payload = json.dumps(
        {"ts": ts_utc, "id": int(cursor.id)}, separators=(",", ":")
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_keyset_cursor(raw: str) -> KeysetCursor:
    """Deserialize and validate a base64url keyset cursor token."""
    cleaned = raw.strip()
    if not cleaned:
        raise ValueError("Keyset cursor must not be empty")
    padding = "=" * ((4 - len(cleaned) % 4) % 4)
    try:
        decoded_bytes = base64.urlsafe_b64decode(cleaned + padding)
        obj = json.loads(decoded_bytes.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Malformed keyset pagination cursor") from exc

    if not isinstance(obj, dict) or "ts" not in obj or "id" not in obj:
        raise ValueError("Keyset cursor is missing required fields")

    ts_raw = obj["ts"]
    id_raw = obj["id"]
    if not isinstance(ts_raw, str) or not isinstance(id_raw, int) or id_raw <= 0:
        raise ValueError("Invalid keyset cursor field types")

    try:
        if ts_raw.endswith("Z"):
            parsed_dt = datetime.fromisoformat(ts_raw[:-1]).replace(tzinfo=UTC)
        else:
            parsed_dt = ensure_utc_datetime(datetime.fromisoformat(ts_raw))
    except ValueError as exc:
        raise ValueError("Invalid keyset cursor timestamp") from exc

    return KeysetCursor(created_at=parsed_dt, id=id_raw)
