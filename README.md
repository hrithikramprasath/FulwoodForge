# Fulwood Forge

A secondary school curriculum resource studio that turns a teacher's pedagogical configuration into a lesson-ready **worksheet, PowerPoint, or multi-lesson booklet** in the Fulwood Academy format, with the teacher mark scheme generated as a separate export.

Output follows the **Fulwood lesson spine** — Do It Now -> key spellings -> keyword bank -> I DO -> misconception check -> WE DO -> YOU DO -> exam language coach -> exam questions -> exit ticket — with mathematics typeset as native Office Open XML Math (OMML) in Word/PowerPoint and KaTeX in PDF and browser previews.

## Stack

- **Frontend** — React 19, TypeScript, Vite, Tailwind CSS v4, DOMPurify, KaTeX (Anti-Vibe Oxford Navy `#0F4C81` & Academic Slate `#0F172A` design system with self-hosted `@fontsource-variable/*` fonts)
- **Backend** — Python 3.12, FastAPI, Pydantic v2, `asyncmy`, `argon2-cffi`, `uv`
- **Database** — MySQL 8.4 LTS (`InnoDB`, `utf8mb4_0900_ai_ci`, `BINARY(16)` RFC 9562 `UUIDv7` public identifiers, `DECIMAL(19,4)` token ledger & immutable audit trail)
- **Authentication** — Native username + Argon2id password authentication with database-backed `ff_session` (`HttpOnly; SameSite=Lax`) cookies, RBAC (`TEACHER` / `ADMIN`), and registration abuse controls (`INVITE_ONLY`, `ADMIN_APPROVAL`, `OPEN`)
- **AI** — Google Gemini (`gemini-3.5-flash-lite` default) using structured outputs and exponential backoff retry
- **Exports** — `.docx` (`python-docx` + Pandoc OMML), `.pptx` (`python-pptx` + Pandoc OMML), `.pdf` (`render.mjs` + headless Chrome with sandbox enabled), plus separate `answers-docx` and `answers-pdf` teacher mark schemes

## Running with Docker Compose

```bash
cp .env.example .env
# Edit .env with your GEMINI_API_KEY, MYSQL_PASSWORD, MYSQL_ROOT_PASSWORD, and BOOTSTRAP_ADMIN_PASSWORD (>= 12 chars)
docker compose up --build -d
python3 deploy/check-gate.py http://127.0.0.1:8000
```

## Verification Pipeline (`./init.sh`)

Run the 7-stage cost-ordered verification harness from the repository root:

```bash
./init.sh
```

And to verify the locked production container image offline (sandbox, CSRF/session guards, and all five export formats):

```bash
docker build -t fulwood-forge:check .
deploy/verify-image.sh fulwood-forge:check
```

## Documentation

| Doc | Purpose |
|---|---|
| [`SPEC.md`](SPEC.md) | Authoritative v2 architecture & security specification |
| [`AGENTS.md`](AGENTS.md) | Engineering constitution & 7-stage verification gate |
| [`SHIP.md`](SHIP.md) | Deploying the MySQL 8.4 + FastAPI stack |
| [`GUIDE.md`](GUIDE.md) | Day-to-day operations, admin workflows, and troubleshooting |
| [`SECURITY.md`](SECURITY.md) | Security controls, process isolation, and verification |
