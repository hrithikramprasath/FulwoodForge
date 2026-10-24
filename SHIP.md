# Deploying Fulwood Forge (`SHIP.md`)

Fulwood Forge runs as a two-service `docker compose` stack:
1. **`mysql` (`mysql:8.4` LTS):** Relational store (`InnoDB`, `utf8mb4_0900_ai_ci`) backed by the persistent `mysql_data` volume on an internal bridge network (`internal_db`).
2. **`app` (`fulwood-forge:local`):** FastAPI backend, static React studio frontend, and sandboxed DOCX/PPTX/PDF export engine bound to `127.0.0.1:8000` (and `127.0.0.1:4180`).

Authentication is built directly into the application (Argon2id password hashing, `ff_session` cookies, and `INVITE_ONLY` / `ADMIN_APPROVAL` / `OPEN` registration modes). No external OAuth proxy or email allowlist is required.

---

## 1. Initial Setup

```bash
# 1. Create and configure .env (gitignored)
cp .env.example .env
```

Edit `.env` and set:
- `GEMINI_API_KEY`: Your Google Gemini API key (`GEMINI_MODEL` defaults to `gemini-3.5-flash-lite`).
- `MYSQL_PASSWORD` and `MYSQL_ROOT_PASSWORD`: Strong random hex secrets (`python3 -c "import secrets; print(secrets.token_hex(24))"`).
- `BOOTSTRAP_ADMIN_USERNAME` and `BOOTSTRAP_ADMIN_PASSWORD`: Initial administrator credentials (`BOOTSTRAP_ADMIN_PASSWORD` must be at least 12 characters).

---

## 2. Build, Start & Verify

```bash
# Validate Compose configuration
docker compose config --quiet

# Build and start MySQL 8.4 + Fulwood Forge app
docker compose up --build -d

# Verify readiness, native auth status, and RFC 9457 unauthenticated rejection
python3 deploy/check-gate.py http://127.0.0.1:8000
```

To run the offline container sandbox and 5-format export verification against the built image:

```bash
deploy/verify-image.sh fulwood-forge:local
```

---

## 3. First Sign-In & Onboarding Teachers

1. Open `http://127.0.0.1:8000` (or your HTTPS reverse-proxy/Funnel URL) and sign in with `BOOTSTRAP_ADMIN_USERNAME` and `BOOTSTRAP_ADMIN_PASSWORD`.
2. Complete the mandatory first-login password change dialog (`must_change_password`).
3. Open **Admin** in the top navigation bar -> **Invites** tab to generate single-use `FF-XXXX-XXXX-XXXX` invite codes for teachers, or switch the registration mode in **Settings**.

---

## 4. Scheduled Maintenance

To keep OS packages and Google Chrome patched daily with automatic health/gate verification and image rollback on failure, add the hourly maintenance trigger via `crontab -e`:

```cron
15 * * * * /absolute/path/to/FulwoodForge/deploy/maintenance.sh
```
