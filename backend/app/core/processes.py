"""Isolated subprocess execution for untrusted document processing (`REQ-SEC-06` / `VULN-06`).

Provides:
- `renderer_env()`: Minimal environment allowlist that strips `GEMINI_API_KEY`, `MYSQL_PASSWORD`, etc.
- `run_worker_async(action, payload, timeout)`: Non-blocking `asyncio.create_subprocess_exec`
  worker runner with `start_new_session=True` and `os.killpg(proc.pid, signal.SIGKILL)` on timeout.
- `run_worker(mode, payload, timeout)`: Synchronous worker runner preserved for CLI/legacy callers.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

_MAX_WORKER_OUTPUT_BYTES: int = 64 * 1024 * 1024
_BACKEND_DIR: Path = Path(__file__).resolve().parents[2]


def renderer_env() -> dict[str, str]:
    """Return a minimal environment allowlist without application secrets."""
    allowed = (
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "TMPDIR",
        "SYSTEMROOT",
        "PLAYWRIGHT_BROWSERS_PATH",
        "PYTHONPATH",
    )
    return {key: os.environ[key] for key in allowed if key in os.environ}


def _worker_env() -> dict[str, str]:
    """Return `renderer_env()` with the absolute `backend/` directory first on `PYTHONPATH`.

    Guarantees `python -m app.services.worker` resolves identically whether the parent
    process was started from the repository root or from `backend/`.
    """
    env = renderer_env()
    existing = env.get("PYTHONPATH")
    backend = str(_BACKEND_DIR)
    env["PYTHONPATH"] = f"{backend}{os.pathsep}{existing}" if existing else backend
    return env


async def run_worker_async(
    action: str,
    payload: bytes,
    timeout: float = 180.0,
) -> bytes:
    """Execute `app.services.worker` non-blockingly using `asyncio.create_subprocess_exec` (`REQ-SEC-06`).

    Kills the entire process group with `SIGKILL` on timeout or cancellation.
    """
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.services.worker",
        action,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_worker_env(),
        cwd=str(_BACKEND_DIR),
        start_new_session=True,
    )
    try:
        stdout_bytes, _stderr_bytes = await asyncio.wait_for(
            proc.communicate(payload),
            timeout=max(0.01, float(timeout)),
        )
        if proc.returncode:
            raise RuntimeError(f"{action} worker failed (exit {proc.returncode})")
        if len(stdout_bytes) > _MAX_WORKER_OUTPUT_BYTES:
            raise ValueError("Worker output exceeds 64 MiB")
        return stdout_bytes
    except TimeoutError as exc:
        raise TimeoutError(f"{action} exceeded its processing deadline") from exc
    finally:
        if proc.pid is not None:
            with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                os.killpg(proc.pid, signal.SIGKILL)
        with contextlib.suppress(ProcessLookupError, OSError):
            await proc.wait()


def run_worker(mode: str, payload: bytes, timeout: float) -> bytes:
    """Kill an isolated operation on deadline; spool output to avoid pipe deadlocks."""
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        proc = subprocess.Popen(
            [sys.executable, "-m", "app.services.worker", mode],
            stdin=subprocess.PIPE,
            stdout=output,
            stderr=errors,
            env=_worker_env(),
            cwd=str(_BACKEND_DIR),
            start_new_session=True,
        )
        try:
            proc.communicate(payload, timeout=max(0.01, timeout))
            if proc.returncode:
                raise RuntimeError(f"{mode} worker failed (exit {proc.returncode})")
            output.seek(0)
            result = output.read(_MAX_WORKER_OUTPUT_BYTES + 1)
            if len(result) > _MAX_WORKER_OUTPUT_BYTES:
                raise ValueError("Worker output exceeds 64 MiB")
            return result
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"{mode} exceeded its processing deadline") from exc
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
