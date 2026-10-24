# Fulwood Forge — Master Enterprise System Specification (`SPEC.md`)

**Version:** 2.0.0 (Native Auth, Admin Control Center & MySQL 8.4 LTS Edition)  
**Status:** Authoritative Baseline (Wave 0 Locked)  
**Notation:** EARS (Easy Approach to Requirements Syntax — `UBIQUITOUS`, `EVENT-DRIVEN`, `STATE-DRIVEN`, `UNWANTED BEHAVIOR`)

---

## 1. Explicit Non-Goals (Out of Scope)

1. **NG-01 (No External OAuth or Email Gateways):** The system SHALL NOT depend on `oauth2-proxy`, Google OAuth2, OIDC providers, `X-Forwarded-Email` proxy headers, email addresses, or SMTP/transactional email delivery.
2. **NG-02 (No Student Accounts or Real-Time Multi-Cursor Co-Editing):** The system is strictly an educator and administrator platform; student-facing logins, live WebSockets co-editing, and external payment/billing gateways (Stripe) are out of scope.

---

## 2. Native Username + Password Auth & Admin Control Architecture

### 2.1 Username-Only Identity & Argon2id Credential Security
- **REQ-AUTH-01 (Username Identity):** The system SHALL identify every user exclusively by a normalized `username` matching `^[a-zA-Z0-9_.-]{3,32}$` (stored canonical lowercase in `users.username` alongside `users.display_name` preserving display casing) and a `password` between `10` and `128` Unicode characters. Zero email columns SHALL exist in the database.
- **REQ-AUTH-02 (Argon2id Hashing):** WHEN a user registers, changes their password, or receives an administrator password reset, THE SYSTEM SHALL hash the plaintext password using `argon2-cffi` (`Type.ID`, `memory_cost=65536` [64 MiB], `time_cost=3`, `parallelism=1`, `hash_len=32`, `salt_len=16`) before persisting to `users.password_hash`. Plaintext passwords SHALL NEVER be logged or cached.
- **REQ-AUTH-03 (Brute-Force Rate Limiting):** IF a client submits more than `5` failed login attempts within a `15-minute` sliding window for the same `(username, ip_address)` pair, THEN THE SYSTEM SHALL reject subsequent login attempts with HTTP `429 Too Many Requests` (`application/problem+json`) and record a `LOGIN_RATE_LIMITED` row in `audit_events`.

### 2.2 Registration Abuse Gate (`INVITE_ONLY`, `ADMIN_APPROVAL`, `OPEN`)
- **REQ-AUTH-04 (Registration Mode Enforcement):** WHEN `POST /api/auth/register` is invoked, THE SYSTEM SHALL inspect `system_settings.registration_mode`:
  1. **WHILE `registration_mode = 'INVITE_ONLY'` (Default):** THE SYSTEM SHALL require a valid, active, un-expired `invite_code` from `invite_codes` where `uses_count < max_uses`, atomically increment `uses_count` via `SELECT ... FOR UPDATE`, and create the user with `status = 'ACTIVE'`.
  2. **WHILE `registration_mode = 'ADMIN_APPROVAL'`:** THE SYSTEM SHALL create the user account with `status = 'PENDING'` and return HTTP `202 Accepted` instructing the teacher that an Administrator must approve the account before sign-in.
  3. **WHILE `registration_mode = 'OPEN'`:** THE SYSTEM SHALL create the user account with `status = 'ACTIVE'`.

### 2.3 Database-Backed Sessions (`ff_session`)
- **REQ-AUTH-05 (Session Issuance & Validation):** WHEN an `ACTIVE` user authenticates via `POST /api/auth/login`, THE SYSTEM SHALL generate a cryptographically random 256-bit (32-byte) opaque token (`secrets.token_urlsafe(32)`), store its SHA-256 digest (`token_sha256 BINARY(32)`) in `user_sessions` with a 24-hour sliding expiration (`expires_at`), and set the `ff_session` cookie with `HttpOnly; Secure; SameSite=Lax; Path=/`.
- **REQ-AUTH-06 (Forced Password Change Gate):** WHILE an authenticated user has `users.must_change_password = 1`, THE SYSTEM SHALL block all `/api/generate`, `/api/export/*`, and `/api/lessons/*` calls with HTTP `403 Forbidden` (`type: "urn:fulwoodforge:error:password-change-required"`) until the user completes `POST /api/auth/change-password`.

### 2.4 Role-Based Access Control (`USER`, `ADMIN`) & Admin Password Reset
- **REQ-ADMIN-01 (Bootstrap Admin):** WHEN the FastAPI application starts, IF zero rows exist in `users` with `role = 'ADMIN'`, THEN THE SYSTEM SHALL bootstrap the initial administrator account from environment variables `BOOTSTRAP_ADMIN_USERNAME` and `BOOTSTRAP_ADMIN_PASSWORD` and record a `BOOTSTRAP_ADMIN_CREATED` event in `audit_events`.
- **REQ-ADMIN-02 (Admin User & Password Management):** WHILE authenticated with `role = 'ADMIN'`, an administrator SHALL be able to:
  1. Issue a one-time temporary password for any user (`POST /api/admin/users/{uuid}/reset-password`), which hashes the temporary password with Argon2id, sets `must_change_password = 1`, and revokes all existing `user_sessions` for that user (`revoked_at = UTC_TIMESTAMP(6)`).
  2. Approve `PENDING` users, suspend `ACTIVE` users (`status = 'SUSPENDED'`, immediately revoking their active sessions), or reactivate `SUSPENDED` users.
  3. Update any user's `role` (`'USER'` / `'ADMIN'`) and individual `hourly_generation_limit`.
  4. Create, list, and deactivate `invite_codes` with configurable `max_uses` and `expires_at`.
  5. Update global `system_settings` (`registration_mode`, `default_hourly_quota`, `maintenance_mode`, `allowed_subjects_json`, `allowed_task_types_json`).

---

## 3. Dual-Plane Logging & Audit Telemetry

- **REQ-LOG-01 (Structured JSON Application Logger):** Every HTTP request, background worker execution, database error, and authentication event SHALL emit a single-line structured JSON record to `stdout` containing `timestamp` (ISO-8601 UTC with microseconds), `level`, `request_id` (`UUIDv7`), `user_id`, `username`, `event`, `path`, `method`, `status_code`, and `duration_ms`.
- **REQ-LOG-02 (Relational Audit Trail — `audit_events`):** Every login success, login failure, logout, registration, password change, admin password reset, user status/quota mutation, system setting change, lesson generation, and document export SHALL insert an immutable row into `audit_events`.
- **REQ-LOG-03 (Token & Cost Ledger — `token_ledger`):** Every Gemini API invocation (and cache hit) SHALL insert a row into `token_ledger` recording `user_id`, `generation_id`, `model_name`, `prompt_tokens`, `candidate_tokens`, `total_tokens`, `estimated_cost_usd DECIMAL(19,4) NOT NULL`, `latency_ms`, `cache_hit`, and `status` (`'SUCCESS'`, `'CACHE_HIT'`, `'UPSTREAM_ERROR'`).

---

## 4. MySQL 8.4 LTS (InnoDB) 3NF Schema (`utf8mb4_0900_ai_ci`)

All tables SHALL use `ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci`, `BIGINT UNSIGNED AUTO_INCREMENT` internal primary keys, `BINARY(16)` RFC 9562 `UUIDv7` public identifiers (`uuid_v7`), `DATETIME(6)` UTC timestamps, and reversible `UP` / `DOWN` migration scripts in `backend/migrations/`.

```sql
CREATE TABLE system_settings (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    registration_mode ENUM('INVITE_ONLY', 'ADMIN_APPROVAL', 'OPEN') NOT NULL DEFAULT 'INVITE_ONLY',
    default_hourly_quota INT UNSIGNED NOT NULL DEFAULT 30,
    maintenance_mode TINYINT(1) NOT NULL DEFAULT 0,
    allowed_subjects_json JSON NOT NULL,
    allowed_task_types_json JSON NOT NULL,
    updated_by_user_id BIGINT UNSIGNED NULL,
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE users (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    uuid_v7 BINARY(16) NOT NULL UNIQUE,
    username VARCHAR(32) NOT NULL UNIQUE,
    display_name VARCHAR(64) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role ENUM('USER', 'ADMIN') NOT NULL DEFAULT 'USER',
    status ENUM('ACTIVE', 'PENDING', 'SUSPENDED') NOT NULL DEFAULT 'ACTIVE',
    must_change_password TINYINT(1) NOT NULL DEFAULT 0,
    hourly_generation_limit INT UNSIGNED NOT NULL DEFAULT 30,
    last_login_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    INDEX idx_users_status_created (status, created_at DESC, id DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE user_sessions (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    uuid_v7 BINARY(16) NOT NULL UNIQUE,
    user_id BIGINT UNSIGNED NOT NULL,
    token_sha256 BINARY(32) NOT NULL UNIQUE,
    ip_address VARCHAR(45) NOT NULL,
    user_agent VARCHAR(255) NOT NULL,
    expires_at DATETIME(6) NOT NULL,
    revoked_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_sessions_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_sessions_user_active (user_id, revoked_at, expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE invite_codes (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    uuid_v7 BINARY(16) NOT NULL UNIQUE,
    code VARCHAR(32) NOT NULL UNIQUE,
    max_uses INT UNSIGNED NOT NULL DEFAULT 1,
    uses_count INT UNSIGNED NOT NULL DEFAULT 0,
    expires_at DATETIME(6) NULL,
    created_by_user_id BIGINT UNSIGNED NOT NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_invites_creator FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE RESTRICT,
    INDEX idx_invites_active_created (is_active, created_at DESC, id DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE lesson_generations (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    uuid_v7 BINARY(16) NOT NULL UNIQUE,
    user_id BIGINT UNSIGNED NOT NULL,
    request_hash BINARY(32) NOT NULL,
    subject VARCHAR(64) NOT NULL,
    year_group VARCHAR(32) NOT NULL,
    tier VARCHAR(32) NULL,
    exam_board VARCHAR(32) NULL,
    topic VARCHAR(1000) NOT NULL,
    task_type ENUM('worksheet', 'powerpoint', 'booklet') NOT NULL,
    title VARCHAR(255) NOT NULL,
    content_json JSON NOT NULL,
    slide_count INT UNSIGNED NOT NULL DEFAULT 0,
    is_archived TINYINT(1) NOT NULL DEFAULT 0,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_generations_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_generations_user_hash_created (user_id, request_hash, created_at DESC),
    INDEX idx_generations_user_keyset (user_id, is_archived, created_at DESC, id DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE artifacts (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    uuid_v7 BINARY(16) NOT NULL UNIQUE,
    generation_id BIGINT UNSIGNED NOT NULL,
    user_id BIGINT UNSIGNED NOT NULL,
    format ENUM('docx', 'pptx', 'pdf', 'answers-docx', 'answers-pdf') NOT NULL,
    file_path VARCHAR(512) NULL,
    sha256_checksum BINARY(32) NOT NULL,
    byte_size BIGINT UNSIGNED NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_artifacts_generation FOREIGN KEY (generation_id) REFERENCES lesson_generations(id) ON DELETE CASCADE,
    CONSTRAINT fk_artifacts_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_artifacts_gen_format (generation_id, format)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE idempotency_keys (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT UNSIGNED NOT NULL,
    idempotency_key VARCHAR(64) NOT NULL,
    request_hash BINARY(32) NOT NULL,
    status ENUM('IN_PROGRESS', 'COMPLETED', 'FAILED') NOT NULL DEFAULT 'IN_PROGRESS',
    response_status SMALLINT UNSIGNED NULL,
    response_body_json JSON NULL,
    expires_at DATETIME(6) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    UNIQUE KEY uq_idempotency_user_key (user_id, idempotency_key),
    CONSTRAINT fk_idempotency_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_idempotency_expires (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE token_ledger (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    uuid_v7 BINARY(16) NOT NULL UNIQUE,
    user_id BIGINT UNSIGNED NOT NULL,
    generation_id BIGINT UNSIGNED NULL,
    model_name VARCHAR(64) NOT NULL,
    prompt_tokens INT UNSIGNED NOT NULL DEFAULT 0,
    candidate_tokens INT UNSIGNED NOT NULL DEFAULT 0,
    total_tokens INT UNSIGNED NOT NULL DEFAULT 0,
    estimated_cost_usd DECIMAL(19,4) NOT NULL DEFAULT 0.0000,
    latency_ms INT UNSIGNED NOT NULL DEFAULT 0,
    cache_hit TINYINT(1) NOT NULL DEFAULT 0,
    status ENUM('SUCCESS', 'CACHE_HIT', 'UPSTREAM_ERROR') NOT NULL DEFAULT 'SUCCESS',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_ledger_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT fk_ledger_generation FOREIGN KEY (generation_id) REFERENCES lesson_generations(id) ON DELETE SET NULL,
    INDEX idx_ledger_user_created (user_id, created_at DESC, id DESC),
    INDEX idx_ledger_created_keyset (created_at DESC, id DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE audit_events (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    uuid_v7 BINARY(16) NOT NULL UNIQUE,
    actor_user_id BIGINT UNSIGNED NULL,
    target_user_id BIGINT UNSIGNED NULL,
    event_type VARCHAR(64) NOT NULL,
    ip_address VARCHAR(45) NOT NULL,
    metadata_json JSON NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_audit_actor FOREIGN KEY (actor_user_id) REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT fk_audit_target FOREIGN KEY (target_user_id) REFERENCES users(id) ON DELETE SET NULL,
    INDEX idx_audit_keyset (created_at DESC, id DESC),
    INDEX idx_audit_actor_keyset (actor_user_id, created_at DESC, id DESC),
    INDEX idx_audit_type_keyset (event_type, created_at DESC, id DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
```

---

## 5. Mandatory Remediation of Audit Vulnerabilities (`VULN-01` – `VULN-11`) & API Contracts

1. **REQ-SEC-01 (`VULN-01` — XSS Sanitization & Strict CSP):**
   - WHEN rendering any Markdown/KaTeX string in `ResultView.tsx` or `frontend/renderer/render.mjs`, THE SYSTEM SHALL sanitize the HTML output through `DOMPurify.sanitize()` and escape all inline raw `<` characters outside math delimiters.
   - `SecurityHeadersMiddleware` in `backend/app/core/hardening.py` SHALL emit `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'`.
2. **REQ-SEC-02 (`VULN-02` — Deterministic Slide Count Without Throwaway PPTX Subprocess):**
   - WHEN `task_type == "powerpoint"`, THE SYSTEM SHALL compute the exact slide count deterministically in pure Python from the `GeneratedContent` structure (`calculate_slide_count(content)`) in `< 1ms` without spawning a second `run_worker("slides")` subprocess or building/discarding a `.pptx` archive.
3. **REQ-SEC-03 (`VULN-03` — Per-User Rate Limiting After Cache Lookup):**
   - THE SYSTEM SHALL scope generation rate limits (`users.hourly_generation_limit`) and export rate limits (`20/min`) strictly by authenticated `user_id` (never process-global), and SHALL check recent `lesson_generations` cache hits *before* consuming a user's hourly Gemini generation quota.
4. **REQ-SEC-04 (`VULN-04` — Fail-Closed Native Session Auth):**
   - THE SYSTEM SHALL authenticate all `/api/*` endpoints (except `/api/auth/login`, `/api/auth/register`, `/api/auth/status`, `/health/live`, `/health/ready`) exclusively via the `ff_session` database-backed session cookie. `oauth2-proxy` and `X-Forwarded-Email` trust SHALL be completely removed.
5. **REQ-SEC-05 (`VULN-05` — Safe XML Deserialization Everywhere):**
   - THE SYSTEM SHALL route 100% of XML parsing in `docx_writer.py`, `pptx_writer.py`, `omml.py`, and `export.py` through `safe_xml_fromstring()` (`resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False`). Raw `docx.oxml.parse_xml()` on Pandoc output and `xml.etree.ElementTree.fromstring()` SHALL be removed.
6. **REQ-SEC-06 (`VULN-06` — Non-Blocking Async Subprocess & Database Execution):**
   - WHEN an `async def` FastAPI endpoint executes a worker subprocess (`processes.py`) or database query, THE SYSTEM SHALL use `asyncio.create_subprocess_exec()` and an async MySQL connection pool (`asyncmy` / `aiomysql`) so the Uvicorn event loop is never blocked.
7. **REQ-SEC-07 (`VULN-07` — Removal of HTTP 200 Fallback Stub & Deterministic 3-Attempt Retry Math):**
   - THE SYSTEM SHALL delete `_get_fallback_content()` from production paths. WHEN Gemini returns a transient `5xx` or `429` error, THE SYSTEM SHALL retry at most `3` attempts with deterministic exponential backoff (`100ms`, `200ms`, `400ms`) and `0` retries on `4xx` errors. IF all 3 attempts fail, THE SYSTEM SHALL return HTTP `502 Bad Gateway` or `503 Service Unavailable` formatted as RFC 9457 `application/problem+json`.
8. **REQ-SEC-08 (`VULN-08` — Complete Answer Key & Mark Scheme Rendering):**
   - WHEN displaying `ResultView.tsx`, THE SYSTEM SHALL enable the `Answer Key & Mark Scheme` tab whenever `hasAnswers` is true, and SHALL render `din.questions[].answer_md`, `we_do.worked_answer_md`, `you_do` tier answers, `exam_questions[].answer_md`, `exit_ticket_answers[]`, and `teacher_answer_key[]`.
9. **REQ-SEC-09 (`VULN-09` — Prompt Isolation, Strict Enums & Scoped LaTeX Repair):**
   - `GenerateRequest.subject` and `GenerateRequest.exam_board` SHALL be restricted to strict `Literal` enums in `backend/app/schemas/content.py`. System instructions SHALL be passed via `GenerateContentConfig(system_instruction=...)` rather than concatenated with user topic text. `_LATEX_ESCAPE_FIX` SHALL only operate inside math delimiters (`$...$`, `$$...$$`) so code indentation (`\t`, `\r`) in Computing lessons is never corrupted.
10. **REQ-SEC-10 (`VULN-10` — MySQL 8.4 Replacing Ephemeral SQLite `cache_db.py`):**
    - THE SYSTEM SHALL replace `backend/app/services/cache_db.py` with MySQL 8.4 `lesson_generations` persistence and remove `_warn_once()` error silencing.
11. **REQ-SEC-11 (`VULN-11` — `AbortController` Request Lifecycle Management):**
    - THE SYSTEM SHALL attach an `AbortController` signal to all `/api/generate` and `/api/export/*` fetches in the React UI and abort in-flight requests on component unmount, user cancellation, or workspace reset.
12. **REQ-API-01 (RFC 9457 Problem Details Contract):**
    - 100% of HTTP error responses (`400`, `401`, `403`, `404`, `409`, `413`, `422`, `429`, `500`, `502`, `503`) SHALL be returned with `Content-Type: application/problem+json` containing `type`, `title`, `status`, `detail`, `instance`, and (for `422`) `invalid-params: [{name, reason}]`.
13. **REQ-API-02 (Idempotency-Key Enforcement):**
    - `POST /api/generate` SHALL accept an `Idempotency-Key` header (`16..64` chars) and synchronize concurrent duplicate submissions in `idempotency_keys` via `SELECT ... FOR UPDATE`.
14. **REQ-API-03 (Health & Readiness Probes):**
    - THE SYSTEM SHALL expose `GET /health/live` (process liveness) and `GET /health/ready` (verifying MySQL 8.4 connection pool `SELECT 1` and Pandoc/Chrome availability).

---

## 6. Anti-Vibe 5-State UI & Admin Control Center Views

### 6.1 `:root` CSS Custom Property Design Token Constitution
`frontend/src/index.css` SHALL define and enforce the following `:root` tokens (Deep Academic Slate & Oxford Navy — zero `#762bfa` MagicSchool purple, zero neon gradients, zero emojis):
- `--bg-canvas: #F8FAFC`
- `--surface-primary: #FFFFFF`
- `--surface-elevated: #F1F5F9`
- `--text-primary: #0F172A`
- `--text-secondary: #334155`
- `--text-muted: #64748B`
- `--border-subtle: #E2E8F0`
- `--border-strong: #CBD5E1`
- `--accent-primary: #0F4C81`
- `--accent-primary-hover: #0B3861`
- `--accent-subtle: #E0F2FE`
- `--status-success: #15803D`
- `--status-warning: #B45309`
- `--status-danger: #B91C1C`

All numeric counters, quotas, latencies, token counts, costs, and timestamps SHALL apply `font-variant-numeric: tabular-nums`.

### 6.2 The 5-State Interactive Matrix
Every primary view (Auth Gate, Teacher Curriculum Workspace, Saved Lesson Library, and Admin Control Center) SHALL implement:
1. **State 1 — Layout-Matched Skeleton Loading:** Shimmer placeholders matching exact card/table geometry (`CLS = 0`).
2. **State 2 — Zero-Data Empty State:** Clean monochrome vector SVG illustration + descriptive guidance + primary CTA button.
3. **State 3 — Populated Data State:** Asymmetric split-pane workspace with flat `1px solid var(--border-subtle)` borders (zero Nested Card Disease).
4. **State 4 — Inline/Toast RFC 9457 Error State:** Field-level `invalid-params` binding and non-destructive error banners that never wipe out an active lesson preview.
5. **State 5 — Optimistic / Phased Mutation State:** Step-by-step generation progress indicator with elapsed `tabular-nums` timer and an active `Cancel` (`AbortController`) button.

### 6.3 Required Application Views
1. **View 1: Native Auth Gate (`/login`, `/register`, Forced Password Change Modal)**
   - Username + password sign-in and registration tabs, dynamically adapting to `registration_mode` (`INVITE_ONLY` shows required invite code field; `ADMIN_APPROVAL` shows pending approval state; `OPEN` enables immediate sign-in).
   - Forced Password Change modal when `user.must_change_password === true`.
2. **View 2: Teacher Split-Pane Curriculum Workspace (`/`)**
   - **Left Pane:** Collapsible Adaptive Form + Saved Lesson Library list (`GET /api/lessons` with keyset pagination, search/filter by subject/task type, one-click reload, and archive/delete).
   - **Right Pane:** Interactive Lesson Spine preview with drag-and-drop section reordering, inline Markdown/text editing per section (`PATCH /api/lessons/{uuid}`), complete `Student Resource` vs. `Answer Key & Mark Scheme` tabs (rendering all DIN, We Do, You Do, Exam Question, and Exit Ticket answers), and one-click `.docx`, `.pptx`, `.pdf`, `answers-docx`, and `answers-pdf` exports.
3. **View 3: Admin Control Center (`/admin` — visible only when `user.role === 'ADMIN'`)**
   - **Overview Telemetry Bar:** Total users, active sessions, 24h generations, cache hit rate `%`, and cumulative token cost (`$0.0000` `tabular-nums`).
   - **User & Quota Manager:** Table of all users (`username`, `display_name`, `role`, `status`, `hourly_generation_limit`, `last_login_at`) with actions to Approve, Suspend/Reactivate, Edit Quota, Revoke Sessions, and **Generate One-Time Password Reset** (displaying the temporary password once in a copyable modal).
   - **Invite Code Manager:** Create and deactivate invite codes (`code`, `uses_count / max_uses`, `expires_at`).
   - **Platform & Visibility Controls:** Live toggles for `registration_mode` (`INVITE_ONLY` / `ADMIN_APPROVAL` / `OPEN`), `maintenance_mode`, `allowed_subjects`, and `allowed_task_types`.
   - **Live Audit & Usage Stream:** Keyset-paginated table combining `audit_events` and `token_ledger` (`timestamp`, `actor_username`, `event_type`, `subject/topic`, `model_name`, `total_tokens`, `estimated_cost_usd`, `latency_ms`, `ip_address`).

[CANARY: MYSQL-8.4-ANTI-VIBE-LOCKED]
