# Operating Guide (`GUIDE.md`)

Everyday terminal and administrative commands for operating **Fulwood Forge**.
See [`SHIP.md`](SHIP.md) for initial deployment and [`SPEC.md`](SPEC.md) for architectural specifications.

## Stack Overview

```text
Teacher / Admin Browser
        |
        v
  app (127.0.0.1:8000 / 127.0.0.1:4180)
  FastAPI + React Studio + Export Workers + Native Auth (ff_session)
        |
        v  (internal_db bridge network)
  mysql (MySQL 8.4 LTS, persistent mysql_data volume)
```

---

## 1. Start, Stop & Recreate

```bash
# Start both mysql and app containers in the background
docker compose up -d

# Rebuild and recreate the app container after code or .env changes
docker compose up --build -d app

# Stop containers without removing data
docker compose stop

# Stop and remove containers (the mysql_data volume is preserved; never pass -v in production)
docker compose down
```

---

## 2. Health & Readiness Checks

```bash
# Check container states (both should show Up and (healthy))
docker compose ps

# Run the automated readiness and native auth gate check
python3 deploy/check-gate.py http://127.0.0.1:8000
```

- `GET /health/live` returns `200 OK` (`{"status": "alive"}`).
- `GET /health/ready` verifies the MySQL `SELECT 1` ping and `pandoc` binary availability (`200 OK` when ready, `503 application/problem+json` if draining or disconnected).

---

## 3. Logs & Diagnostics

```bash
docker compose logs --tail=100 app
docker compose logs --tail=100 mysql
docker compose logs -f app
```

All application logs are emitted as single-line structured JSON objects with `request_id` (`UUIDv7`), `event`, `status_code`, and `latency_ms`.

---

## 4. Managing Users, Invite Codes & Quotas

User management is handled directly inside the web UI's **Admin Control Center** (`/admin`):

- **Onboard a teacher (`INVITE_ONLY` mode):** Sign in as an `ADMIN`, open **Admin -> Invites**, click **Create Invite**, and share the single-use `FF-XXXX-XXXX-XXXX` code.
- **Approve pending accounts (`ADMIN_APPROVAL` mode):** Open **Admin -> Users** and click **Approve** on any `PENDING_APPROVAL` row.
- **Reset a forgotten password:** Open **Admin -> Users**, click **Reset Password**, and provide the one-time temporary password to the teacher (they will be required to choose a new password immediately on sign-in).
- **Adjust daily generation quotas or suspend an account:** Open **Admin -> Users** to update per-user daily limits or status (`ACTIVE` / `SUSPENDED`), or open **Admin -> Settings** to adjust the system-wide default quota.
- **Audit security events & token costs:** Open **Admin -> Audit & Ledger** to inspect immutable `audit_events` and `token_ledger` (`DECIMAL(19,4)` USD cost) records.

---

## 5. Daily Maintenance & Rollback

```bash
# Dry-run candidate build and offline sandbox verification without replacing production
deploy/monthly-rebuild.sh --dry-run

# Build, verify, swap image, and run health/gate checks with automatic rollback
deploy/monthly-rebuild.sh

# Check maintenance status
deploy/check-rebuild-health.sh
```
