# Fulwood Forge — Autonomous Agent Engineering Constitution (`AGENTS.md`)

> **Authority:** This constitution governs all AI and human engineering sessions in `FulwoodForge`. Every implementation decision must conform to [`SPEC.md`](./SPEC.md) and track state in [`features.json`](./features.json) and [`claude-progress.txt`](./claude-progress.txt).

---

## 1. Unidirectional Dependency Law

All backend (`backend/app/`) and frontend (`frontend/src/`) modules MUST strictly obey the unidirectional import hierarchy:

```text
Types/Schemas -> Config -> Repository (MySQL 8.4) -> Service -> Runtime/Controller -> UI
```

- **Forbidden upward or skip-layer imports:**
  - FastAPI controllers (`backend/app/api/`) MUST NOT import `google-genai`, `python-docx`, `python-pptx`, `sqlite3`, or MySQL repositories directly.
  - Entrypoint runtime (`backend/app/main.py`) MUST NOT run inline storage queries or import repository internals directly.
  - React UI components (`frontend/src/components/`, `frontend/src/App.tsx`) MUST NOT invoke raw `fetch()` calls or un-sanitized `marked.parse()` without going through isolated API client and `DOMPurify` sanitization services.

---

## 2. 7-Stage Cost-Ordered Verification Pipeline (Zero-Warning Gate)

Run verification stages in ascending order of compute cost. Every stage MUST exit `0` before marking any feature `"passes": true` in `features.json`:

1. **Stage 1 — Formatting Check (Instant):**
   ```bash
   uvx ruff format --check backend && npx --prefix frontend prettier --check "src/**/*.{ts,tsx,css}"
   ```
2. **Stage 2 — Static Analysis, Linter & SAST Security Scan (Seconds):**
   ```bash
   uvx ruff check backend && uvx bandit -r backend/app -ll -q && npm --prefix frontend run lint -- --max-warnings=0
   ```
3. **Stage 3 — Compiler Strict Type Check (Seconds):**
   ```bash
   uvx mypy --strict --ignore-missing-imports backend/app && npx --prefix frontend tsc --noEmit
   ```
4. **Stage 4 — Secret & Host Leak Scan (Instant):**
   - Zero hardcoded API keys (`AIza...`), session secrets, Tailscale `.ts.net` hostnames, or local Linux usernames (`/home/hr1th1krr`) in any git-tracked file (`git grep -E "(AIza|\.ts\.net|/home/hr1th1krr)"`).
5. **Stage 5 — Unidirectional Import Audit (Instant):**
   - Verify every Python and TypeScript `import` adheres to `Types -> Config -> Repository -> Service -> Runtime/Controller -> UI`.
6. **Stage 6 — Anti-Reward-Hacking Test Audit (Instant):**
   - Zero deleted, weakened (`status_code < 500`), or skipped (`@pytest.mark.skip`, `it.skip`) assertions.
   - Zero unused-variable suppressions (`void hasAnswers;`), zero embedded `if __name__ == "__main__":` test blocks inside `backend/app/services/`, and CI (`.github/workflows/ci.yml`) MUST execute full `pytest` and `vitest` suites (never `py_compile` only).
7. **Stage 7 — Full Automated Test Suite Execution (Minutes):**
   ```bash
   uvx pytest backend/tests -v && npm --prefix frontend test -- --run
   ```

---

## 3. Three-Tier Boundary Protocol

### ALWAYS DO
- Read [`SPEC.md`](./SPEC.md) and [`features.json`](./features.json) before modifying any file.
- Use **100% parameterized SQL queries** against MySQL 8.4 LTS (`InnoDB`, `utf8mb4_0900_ai_ci`) with `BIGINT UNSIGNED AUTO_INCREMENT` internal PKs paired with `BINARY(16)` RFC 9562 `UUIDv7` public IDs and `DATETIME(6)` UTC timestamps.
- Hash all user passwords and native secrets using **Argon2id** (`argon2-cffi`: `memory_cost=65536`, `time_cost=3`, `parallelism=1`).
- Sanitize all rendered Markdown/KaTeX HTML via **`DOMPurify`** (and escape inline `<` in `frontend/renderer/render.mjs`) and parse all XML via **`safe_xml_fromstring()`**.
- Emit **IETF RFC 9457 `application/problem+json`** (`type`, `title`, `status`, `detail`, `instance`, `invalid-params`) on 100% of `4xx` and `5xx` HTTP error responses.
- Record dual-plane telemetry: single-line **structured JSON logs** to `stdout` AND immutable relational rows in MySQL `audit_events` and `token_ledger` (`DECIMAL(19,4) NOT NULL` for cost accounting).
- Preserve the architectural integrity canary: `[CANARY: MYSQL-8.4-ANTI-VIBE-LOCKED]`.

### ASK FIRST
- Adding any new external runtime dependency to `backend/pyproject.toml` or `frontend/package.json`.
- Altering primary key, foreign key, or composite keyset pagination index definitions on MySQL 8.4 tables.
- Modifying `ff_session` cookie security attributes (`HttpOnly; Secure; SameSite=Lax`) or Argon2id parameters.

### NEVER DO
- **NEVER** use `oauth2-proxy`, Google OAuth headers (`X-Forwarded-Email`), or require email addresses/SMTP for user registration or authentication.
- **NEVER** use `UUIDv4` or `VARCHAR(36)` primary keys, or use `FLOAT` / `DOUBLE` for token cost or quota accounting.
- **NEVER** invoke synchronous blocking calls (`subprocess.Popen.communicate()`, synchronous DB drivers, or CPU-heavy `python-docx`/`python-pptx` builders) directly on the Uvicorn `async def` event loop, or spawn a throwaway PPTX subprocess just to count slides.
- **NEVER** return HTTP `200 OK` fallback stubs (`_get_fallback_content`) when Gemini fails, or retry on `4xx` client/validation errors.
- **NEVER** include emojis (`⚡`, `🔁`, `🔤`, `⚠️`, `📚`, `🎓`, `📌`, `💬`, `👥`, `✏️`, `✅`, `📝`, `🎯`, `🎟️`, `🖊️`, `🌐`, `🔑`) or MagicSchool purple (`#762bfa`) anywhere in `.tsx`, `.ts`, `.css`, `.mjs`, or `.py` files.

[CANARY: MYSQL-8.4-ANTI-VIBE-LOCKED]
