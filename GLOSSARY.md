# Fulwood Forge Curriculum Engine

Fulwood Forge generates and exports secondary school curriculum materials with Gemini AI. It runs on a MySQL 8.4 database, a FastAPI backend, and a React frontend.

## Language

**Teacher**:
An authenticated user with permissions to generate lessons, inspect history, and download artifacts.
_Avoid_: Instructor, tutor, educator, client

**Admin**:
An authenticated user with permissions to manage accounts, reset passwords, adjust quotas, and audit system events.
_Avoid_: Superuser, root, operator

**Lesson**:
A complete curriculum package that includes a lesson plan, slide presentation, worksheet, quiz, and mark scheme.
_Avoid_: Course, unit, study pack

**Artifact**:
A compiled file created from a lesson in DOCX, PPTX, PDF, TEX, or ZIP format.
_Avoid_: Export, download, document file

**Invite Code**:
A single-use token that permits account registration when the system operates in invite-only mode.
_Avoid_: Voucher, promo code, signup token

**Session**:
A database-backed record identified by a secure HTTP-only cookie that authenticates a user.
_Avoid_: Auth token, login state, JWT

**Token Ledger**:
An immutable database table that records Gemini AI token quantities and financial costs.
_Avoid_: Billing table, cost tracker, usage log

**Audit Event**:
An immutable record of a security action or administrative change with timestamp and IP address metadata.
_Avoid_: System log, activity record

**Registration Abuse Gate**:
An access filter that controls account registration through invite codes or administrator approvals.
_Avoid_: Signup wall, registration guard

**Anti-Vibe UI**:
A user interface that uses high-contrast grayscale styling, zero emojis, and zero playful branding.
_Avoid_: Modern theme, clean UI, minimal style
