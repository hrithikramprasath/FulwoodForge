import type {
  AuditEventRecord,
  GeneratedContent,
  InviteCodeRecord,
  SavedLessonSummary,
  TokenLedgerRecord,
  AuthenticatedUser,
} from "@/types/content";

/*
 * Transport-level DTO helpers shared by lib/api.ts and the views.
 * Mirrors the keyset-paginated *PageResponse models in backend/app/api/*.py.
 */

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
  has_more: boolean;
}

export type SavedLessonPage = Page<SavedLessonSummary>;
export type AdminUserPage = Page<AuthenticatedUser>;
export type InviteCodePage = Page<InviteCodeRecord>;
export type AuditEventPage = Page<AuditEventRecord>;
export type TokenLedgerPage = Page<TokenLedgerRecord>;

export interface SavedLessonDetail extends SavedLessonSummary {
  content: GeneratedContent;
}

export interface SavedLessonUpdate {
  title?: string;
  content?: GeneratedContent;
}

export interface InviteCodeCreate {
  code?: string | null;
  max_uses: number;
  expires_in_hours: number | null;
}

export interface SessionRevokeResponse {
  user_uuid: string;
  sessions_revoked: number;
}

/** Outcome of POST /api/auth/register: 201 signed in, 202 pending approval. */
export interface RegisterOutcome {
  httpStatus: number;
  pendingApproval: boolean;
  status: import("@/types/content").AuthStatusResponse;
}

export interface DownloadedFile {
  blob: Blob;
  filename: string | null;
}
