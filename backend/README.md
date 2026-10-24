# Fulwood Forge — Backend

FastAPI backend implementing the Fulwood Academy prompt engine, Gemini structured outputs (`Pydantic`), native Argon2id session authentication, RBAC admin controls, and relational persistence on **MySQL 8.4 LTS**.

## Architecture (`AGENTS.md` Unidirectional Law)

```text
Types/Schemas (app/schemas) -> Config (app/core) -> Repository (app/repositories, MySQL 8.4) -> Service (app/services) -> Runtime/Controller (app/api, app/main.py)
```

## Local Setup

1. Install [`uv`](https://docs.astral.sh/uv/) and ensure MySQL 8.4 LTS is running (or start `docker compose up -d mysql`).
2. Copy the root environment template and configure your credentials:

   ```bash
   cp .env.example .env
   ```

3. Install backend dependencies and run the test suite:

   ```bash
   uv sync --project backend --frozen
   uv run --project backend pytest backend/tests -v
   ```

## Key Subsystems

- **Relational Persistence (`app/db`, `app/repositories`):** Parameterized SQL queries over `asyncmy` connection pooling with `BIGINT UNSIGNED` internal primary keys paired with `BINARY(16)` RFC 9562 `UUIDv7` public identifiers and composite keyset pagination.
- **Native Auth & RBAC (`app/services/auth_service.py`, `app/services/admin_service.py`):** Username + Argon2id (`memory_cost=65536, time_cost=3, parallelism=1`) password hashing, SHA-256 hashed `ff_session` tokens (`user_sessions`), brute-force lockout protection, and registration gates (`INVITE_ONLY`, `ADMIN_APPROVAL`, `OPEN`).
- **Generation, Caching & Idempotency (`app/services/lesson_service.py`, `app/services/gemini.py`):** Per-user SHA-256 canonical request caching in MySQL (`lesson_generations` and `artifacts` tables), `Idempotency-Key` replay protection, per-user hourly quota enforcement, and `DECIMAL(19,4)` token cost accounting in `token_ledger`.
- **Isolated Document Workers (`app/services/export.py`, `app/services/worker.py`):** DOCX, PPTX, and PDF exports run in isolated worker subprocesses with stripped environments, file-size limits, and process-group timeouts.
- **RFC 9457 Problem Details (`app/api/errors.py`):** All `4xx` and `5xx` responses emit standard `application/problem+json` payloads.
