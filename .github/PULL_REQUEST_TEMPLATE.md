## Description
<!-- Describe what this PR changes, why it is necessary, and which SPEC.md requirement(s) it addresses. -->

## Security & Architecture Checklist
- [ ] Unidirectional import hierarchy (`Types -> Config -> Repository -> Service -> Controller -> UI`) preserved.
- [ ] 100% parameterized SQL queries and RFC 9457 `application/problem+json` error responses maintained.
- [ ] Zero hardcoded credentials, `.env` files, private keys, or internal hostnames committed.
- [ ] Zero emojis or `#762bfa` palette tokens introduced.

## Verification (`./init.sh`)
- [ ] `./init.sh` exits `0` across all 7 verification stages (Formatting, Lint/SAST, Strict Types, Secret/Hygiene Scan, Import Audit, Test Integrity Audit, Full Test Suite & Build).
