# Fulwood Forge

> **Notice: Archived & Unmaintained Repository**
> This repository is archived and no longer actively maintained. It is provided for reference and educational purposes only. Issues, pull requests, and ongoing security updates are not monitored or provided.

A secondary school curriculum resource studio that turns a teacher's pedagogical configuration into a lesson-ready **worksheet, PowerPoint, or multi-lesson booklet** in the Fulwood Academy format, with the teacher mark scheme generated as a separate export.

Output follows the **Fulwood lesson spine** — Do It Now -> key spellings -> keyword bank -> I DO -> misconception check -> WE DO -> YOU DO -> exam language coach -> exam questions -> exit ticket — with mathematics typeset as native Office Open XML Math (OMML) in Word/PowerPoint and KaTeX in PDF and browser previews.

## Stack

- **Frontend** — React 19, TypeScript, Vite, Tailwind CSS v4, DOMPurify, KaTeX (Anti-Vibe Oxford Navy `#0F4C81` & Academic Slate `#0F172A` design system with self-hosted `@fontsource-variable/*` fonts)
- **Backend** — Python 3.12, FastAPI, Pydantic v2, `asyncmy`, `argon2-cffi`, `uv`
- **Database** — MySQL 8.4 LTS (`InnoDB`, `utf8mb4_0900_ai_ci`, `BINARY(16)` RFC 9562 `UUIDv7` public identifiers, `DECIMAL(19,4)` token ledger & immutable audit trail)
- **Authentication** — Native username + Argon2id password authentication with database-backed `ff_session` (`HttpOnly; Secure; SameSite=Lax`) cookies, RBAC (`USER` / `ADMIN`), and registration abuse controls (`INVITE_ONLY`, `ADMIN_APPROVAL`, `OPEN`)
- **AI** — Google Gemini (`gemini-3.5-flash-lite` default) using structured outputs and deterministic 3-attempt exponential backoff retry (`100ms`, `200ms`, `400ms`)
- **Exports** — `.docx` (`python-docx` + Pandoc OMML), `.pptx` (`python-pptx` + Pandoc OMML), `.pdf` (`render.mjs` + headless Chrome with seccomp/namespace sandbox enabled), plus separate `answers-docx` and `answers-pdf` teacher mark schemes

## Quick Start (Docker Compose)

1. Copy `.env.example` to `.env` (`chmod 600 .env`) and configure `GEMINI_API_KEY`, `MYSQL_PASSWORD`, `MYSQL_ROOT_PASSWORD`, `BOOTSTRAP_ADMIN_USERNAME`, and `BOOTSTRAP_ADMIN_PASSWORD` (at least 12 characters):

   ```bash
   cp .env.example .env
   chmod 600 .env
   ```

2. Validate configuration, build, and start the two-service stack (`mysql:8.4` on the internal bridge network + `app` bound to `127.0.0.1:8000` and `127.0.0.1:4180`):

   ```bash
   docker compose config --quiet
   docker compose up --build -d
   python3 deploy/check-gate.py http://127.0.0.1:8000
   ```

3. Sign in at `http://127.0.0.1:8000` with `BOOTSTRAP_ADMIN_USERNAME` and `BOOTSTRAP_ADMIN_PASSWORD`, then complete the mandatory first-login password change dialog (`must_change_password`).

## Day-to-Day Operations & Admin Control Center (`/admin`)

- **Health & Readiness Probes:**
  - `GET /health/live` returns `200 OK` (`{"status": "live"}`).
  - `GET /health/ready` verifies the MySQL 8.4 `SELECT 1` connection pool ping and `pandoc` binary availability (`200 OK` with `{"status": "ready", "database": "connected", "pandoc": "available"}`, or `503 application/problem+json` when unhealthy/draining).
- **Onboarding & User Management (`/admin`):**
  - **Invite Codes (`INVITE_ONLY` mode):** Open **Admin -> Invite Codes** to create `FF-XXXX-XXXX` invite codes with configurable `max_uses` and expiration.
  - **Account Approval (`ADMIN_APPROVAL` mode):** Open **Admin -> Users** and click **Approve** on any `PENDING` account.
  - **One-Time Password Reset:** Open **Admin -> Users**, click **Reset password**, and share the generated 16-character temporary password (revokes active sessions and requires an immediate password change on next sign-in).
  - **Hourly Quotas & Platform Visibility:** Update per-user `hourly_generation_limit` or account status (`ACTIVE` / `SUSPENDED`) in **Users**, or configure `registration_mode`, `default_hourly_quota`, `maintenance_mode`, `allowed_subjects`, and `allowed_task_types` in **Platform Settings**.
  - **Audit Trail & Cost Telemetry:** Inspect keyset-paginated `audit_events` and `token_ledger` (`DECIMAL(19,4)` USD cost) records in **Audit Log** and **Token Ledger**.
- **Scheduled Maintenance & Rollback:**
  - Dry-run candidate build and sandbox verification without touching production: `deploy/monthly-rebuild.sh --dry-run`
  - Build, verify, swap image, and run readiness/auth gate checks with automatic rollback: `deploy/monthly-rebuild.sh`
  - Inspect rebuild and security scan state: `deploy/check-rebuild-health.sh`
  - Hourly cron entrypoint (`15 * * * *`): `/absolute/path/to/FulwoodForge/deploy/maintenance.sh`

## Verification Pipeline (`./init.sh`)

Run the 7-stage cost-ordered verification harness from the repository root:

```bash
./init.sh
```

To verify the locked production container image offline (`--read-only`, `--network none`, Chrome seccomp sandbox, CSRF/session guards, and all five export formats):

```bash
docker build -t fulwood-forge:check .
deploy/verify-image.sh fulwood-forge:check
```

## Documentation

| Doc | Purpose |
|---|---|
| [`SPEC.md`](SPEC.md) | Authoritative v2 EARS architecture, schema & security specification |
| [`AGENTS.md`](AGENTS.md) | Engineering constitution, unidirectional import law & 7-stage verification gate |
| [`SECURITY.md`](SECURITY.md) | Defense-in-depth security controls, process isolation & verification |
| [`backend/README.md`](backend/README.md) | FastAPI backend architecture, MySQL 8.4 repositories & local setup |
| [`frontend/README.md`](frontend/README.md) | React 19 + TypeScript studio architecture & commands |
