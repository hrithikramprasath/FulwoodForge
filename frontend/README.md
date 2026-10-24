# Fulwood Forge — Frontend

React 19 + TypeScript + Vite + Tailwind CSS v4 studio interface styled in the **Deep Academic Slate (`#0F172A`) & Oxford Navy (`#0F4C81`)** Anti-Vibe design system (`SPEC.md`).

Fonts (`@fontsource-variable/plus-jakarta-sans` and `@fontsource-variable/jetbrains-mono`) are self-hosted so the browser never makes external font requests under the strict Content Security Policy (`REQ-SEC-01`).

## Commands

```bash
npm --prefix frontend ci
npm --prefix frontend/renderer ci --omit=dev
npm --prefix frontend run dev          # -> http://localhost:5173
npm --prefix frontend run build        # tsc -b && vite build
npm --prefix frontend run lint         # ESLint (--max-warnings=0)
npm --prefix frontend run format:check # Prettier check
npm --prefix frontend test -- --run    # Vitest suite
```

## Directory Layout

| Path | Responsibility |
|---|---|
| `src/lib/api.ts` | Typed HTTP client (`credentials: "include"`), RFC 9457 `ProblemDetails` parser, `Idempotency-Key` (UUIDv7) generator, and `AbortSignal` support |
| `src/lib/sanitize.ts` | Single Markdown + KaTeX rendering gateway sanitized via `DOMPurify` |
| `src/lib/adapt.ts` | Adaptive pedagogical form configuration, subjects, and export format rules |
| `src/components/auth/` | `AuthGate` (Sign In / Create Account tabs) and `ForcedPasswordChangeModal` |
| `src/components/workspace/` | `StudioWorkspace`, `StudioNav`, `AdaptiveForm`, `LessonLibraryDrawer`, and `StudioEmptyState` |
| `src/components/preview/` | `ResultView`, `LessonSections`, `AnswerKeyView`, `LoadingState`, and `ErrorState` |
| `src/components/admin/` | `AdminDashboard`, `OverviewRibbon`, `UsersPanel`, `InvitesPanel`, `SettingsPanel`, and `AuditPanel` |
| `src/components/ui/` | Accessible primitives (Radix UI + Tailwind v4 tokens) |
| `renderer/render.mjs` | Server-side unified/remark/rehype-katex HTML renderer used by headless Chrome for PDF exports |
