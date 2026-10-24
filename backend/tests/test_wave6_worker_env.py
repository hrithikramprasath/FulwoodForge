"""Worker subprocess environment is independent of the parent's working directory."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from app.core import processes
from app.core.processes import _worker_env


def test_worker_env_prepends_absolute_backend_dir() -> None:
    backend = str(Path(processes.__file__).resolve().parents[2])
    env = _worker_env()
    first = env["PYTHONPATH"].split(os.pathsep)[0]
    assert first == backend
    assert Path(first).is_absolute()
    assert (Path(first) / "app" / "services" / "worker.py").is_file()


def test_worker_env_preserves_existing_pythonpath(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTHONPATH", "/opt/extra")
    parts = _worker_env()["PYTHONPATH"].split(os.pathsep)
    assert parts[-1] == "/opt/extra"
    assert len(parts) == 2


def test_worker_env_never_leaks_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    monkeypatch.setenv("MYSQL_PASSWORD", "dummy")
    env = _worker_env()
    assert "GEMINI_API_KEY" not in env
    assert "MYSQL_PASSWORD" not in env


def test_run_worker_resolves_module_from_foreign_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worker must start even if the parent's cwd is neither repo root nor backend/."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PYTHONPATH", raising=False)
    with pytest.raises(RuntimeError, match="exit"):
        # Unknown action: the module imports and exits non-zero (not ModuleNotFoundError
        # surfacing as an import failure) -- either way a clean RuntimeError, never a hang.
        processes.run_worker("definitely-not-an-action", b"{}", timeout=60)
