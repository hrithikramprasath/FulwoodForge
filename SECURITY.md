# Security Architecture

Fulwood Forge enforces defense-in-depth across authentication, input validation, HTML/XML sanitization, subprocess isolation, and container hardening (`SPEC.md` & `AGENTS.md`).

## 1. Native Authentication, Session Security & RBAC

- **Zero External Proxy Trust (`NG-01`, `REQ-SEC-04`):** The backend never trusts `X-Forwarded-Email` or external OAuth proxy headers. All protected `/api/*` endpoints authenticate exclusively against the `ff_session` cookie.
- **Argon2id Password Hashing (`REQ-AUTH-01`):** User passwords and single-use invite codes are hashed with Argon2id (`memory_cost=65536`, `time_cost=3`, `parallelism=1`).
- **Database-Backed Sessions (`REQ-AUTH-02`):** Session cookies store a 256-bit CSPRNG token (`HttpOnly; SameSite=Lax`, plus `Secure` in production); only the `SHA-256` digest is persisted in the MySQL `sessions` table.
- **CSRF Protection:** State-mutating `/api/*` requests (`POST`, `PUT`, `PATCH`, `DELETE`) enforce strict `Origin` and `Sec-Fetch-Site` validation.
- **Brute-Force & Registration Abuse Controls (`REQ-AUTH-03`, `REQ-AUTH-05`):** Dual-keyed sliding-window rate limiters protect `/api/auth/login` and `/api/auth/register`, and registration is governed by the dynamic `registration_mode` setting (`INVITE_ONLY`, `ADMIN_APPROVAL`, or `OPEN`).
- **Object-Level Authorization (`REQ-DATA-03`):** Every `/api/lessons/{generation_uuid}` read, update, export, or delete query scopes ownership to the authenticated `user_id` unless the caller holds the `ADMIN` role.

## 2. XSS, CSP, XXE & Prompt-Injection Defenses

- **Strict Content Security Policy (`REQ-SEC-01`):** Every response carries `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'`, alongside `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, and `Referrer-Policy: no-referrer`.
- **DOMPurify & Renderer Sanitization:** All client-side Markdown/KaTeX passes through `DOMPurify.sanitize()` in `frontend/src/lib/sanitize.ts`, and `frontend/renderer/render.mjs` escapes raw `<` / `>` outside math delimiters before running `remark-parse`.
- **XXE Prevention (`REQ-SEC-06`):** All Office XML parsing in `backend/app/services/omml.py` uses `safe_xml_fromstring()`, which rejects `<!DOCTYPE` and `<!ENTITY` declarations prior to parsing.
- **Structured Input Allowlisting (`REQ-SEC-09`):** `GenerateRequest` validates `subject` and `exam_board` against strict regex/literal constraints and enforces recursive depth, length, and item bounds across `GeneratedContent`.

## 3. Subprocess & Container Isolation

- **Headless Chrome Sandbox (`REQ-SEC-08`):** PDF rendering requires system Google Chrome with `chromium_sandbox=True` under `deploy/chrome-seccomp.json` and `CAP_SYS_CHROOT`. JavaScript is disabled on rendered pages, and network interception permits only the local HTML file and local KaTeX assets.
- **Pandoc Sandbox:** Mathematical LaTeX-to-OMML conversion runs `pandoc --sandbox` with an explicit argument list and a 10-second timeout.
- **Worker Process Groups (`REQ-SEC-07`):** Export workers run in separate process groups (`start_new_session=True`) with stripped environment variables (`renderer_env()` / `_worker_env()`, never leaking `GEMINI_API_KEY` or `MYSQL_PASSWORD`) and `os.killpg(..., signal.SIGKILL)` on timeout.
- **Read-Only Root Filesystem:** The application container runs as non-root `appuser` (`uid 10001`) with `read_only: true`, `no-new-privileges`, and bounded `tmpfs` mounts at `/tmp` and `/home/appuser`.

## 4. Verification Commands

Run the full verification suite and container security checks from the repository root:

```bash
./init.sh
python3 -m unittest discover -s deploy/tests -v
docker build -t fulwood-forge:check .
deploy/verify-image.sh fulwood-forge:check
python3 deploy/check-gate.py http://127.0.0.1:8000
```
