"""RFC 9562 UUIDv7 generator and binary/string conversion utilities.

Leaf module: ZERO internal `app.*` imports.
Produces time-ordered 128-bit identifiers (16-byte `BINARY(16)` for MySQL 8.4 InnoDB
clustered/secondary B+ Tree indexes and canonical 36-character lowercase strings for APIs).
"""

from __future__ import annotations

import re
import secrets
import threading
import time

_UUID7_REGEX = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-7[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)

_lock = threading.Lock()
_last_timestamp_ms: int = 0
_last_rand_a: int = 0


def generate_uuid7_bytes() -> bytes:
    """Generate a 16-byte RFC 9562 UUIDv7 with strict within-millisecond monotonicity.

    Layout (128 bits, big-endian):
      - Bits   0..47 : 48-bit Unix epoch timestamp in milliseconds
      - Bits  48..51 : 4-bit version (0b0111 = 7)
      - Bits  52..63 : 12-bit monotonic/random counter (rand_a)
      - Bits  64..65 : 2-bit RFC 9562 variant (0b10 = 2)
      - Bits  66..127: 62-bit cryptographic randomness (rand_b)
    """
    global _last_timestamp_ms, _last_rand_a

    now_ms = int(time.time() * 1000)
    with _lock:
        if now_ms > _last_timestamp_ms:
            _last_timestamp_ms = now_ms
            # Seed lower 11 bits randomly so up to 2,048 monotonic increments fit in the same ms.
            _last_rand_a = secrets.randbits(11)
        else:
            _last_rand_a += 1
            if _last_rand_a > 0xFFF:
                _last_timestamp_ms += 1
                _last_rand_a = secrets.randbits(11)
        ts_ms = _last_timestamp_ms & 0xFFFFFFFFFFFF
        rand_a = _last_rand_a & 0xFFF

    rand_b = secrets.randbits(62)

    high_64 = (ts_ms << 16) | (0x7 << 12) | rand_a
    low_64 = (0b10 << 62) | rand_b
    value_128 = (high_64 << 64) | low_64
    return value_128.to_bytes(16, byteorder="big")


def uuid7_bytes_to_str(raw: bytes) -> str:
    """Convert a 16-byte RFC 9562 UUIDv7 into its canonical 36-character lowercase string."""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) != 16:
        raise ValueError("UUIDv7 binary value must be exactly 16 bytes")
    version = (raw[6] >> 4) & 0x0F
    variant = (raw[8] >> 6) & 0x03
    if version != 7 or variant != 0b10:
        raise ValueError("Binary bytes do not represent a valid RFC 9562 UUIDv7")
    hex_str = bytes(raw).hex()
    return (
        f"{hex_str[0:8]}-{hex_str[8:12]}-{hex_str[12:16]}-"
        f"{hex_str[16:20]}-{hex_str[20:32]}"
    )


def uuid7_str_to_bytes(val: str) -> bytes:
    """Validate a canonical 36-character RFC 9562 UUIDv7 string and return 16 raw bytes."""
    cleaned = val.strip()
    if len(cleaned) != 36 or not _UUID7_REGEX.match(cleaned):
        raise ValueError(f"Invalid RFC 9562 UUIDv7 string: {val!r}")
    hex_digits = cleaned.replace("-", "").lower()
    return bytes.fromhex(hex_digits)


def generate_uuid7_str() -> str:
    """Generate a canonical lowercase 36-character RFC 9562 UUIDv7 string."""
    return uuid7_bytes_to_str(generate_uuid7_bytes())
