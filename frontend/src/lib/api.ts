import type {
  AdminOverviewMetrics,
  AdminPasswordResetResponse,
  AdminUserUpdateRequest,
  AuthenticatedUser,
  AuthStatusResponse,
  ChangePasswordRequest,
  GeneratedContent,
  LoginRequest,
  RegisterRequest,
  SystemSettings,
  TaskType,
  TeacherRequest,
} from "@/types/content";
import type {
  AdminUserPage,
  AuditEventPage,
  DownloadedFile,
  InviteCodeCreate,
  InviteCodePage,
  RegisterOutcome,
  SavedLessonDetail,
  SavedLessonPage,
  SavedLessonUpdate,
  SessionRevokeResponse,
  TokenLedgerPage,
} from "@/types/api";
import type { InviteCodeRecord, ProblemDetails } from "@/types/content";

/** Raw /api/generate response: validate `body` with generatedContentSchema before use. */
export interface GenerateResult {
  body: unknown;
  generationUuid: string | null;
  cacheHit: boolean;
}

/*
 * The single network boundary of the UI (AGENTS.md Section 1). Components never
 * call fetch(); they call these typed methods, each of which accepts an optional
 * AbortSignal (REQ-SEC-11 / VULN-11) and surfaces failures as ApiProblemError,
 * the parsed IETF RFC 9457 problem document (REQ-API-01).
 */

const API_BASE: string = import.meta.env.VITE_API_URL ?? "";

/** A non-2xx response parsed from `application/problem+json`. */
export class ApiProblemError extends Error {
  readonly status: number;
  readonly title: string;
  readonly detail: string;
  readonly type: string;
  readonly instance: string;
  readonly requestId: string | null;
  readonly retryAfterSeconds: number | null;
  /** `invalid-params` entries keyed by field `name`, value = `reason`. */
  readonly invalidParams: Record<string, string>;

  constructor(init: {
    status: number;
    title: string;
    detail: string;
    type?: string;
    instance?: string;
    requestId?: string | null;
    retryAfterSeconds?: number | null;
    invalidParams?: Record<string, string>;
    cause?: unknown;
  }) {
    super(init.detail || init.title, { cause: init.cause });
    this.name = "ApiProblemError";
    this.status = init.status;
    this.title = init.title;
    this.detail = init.detail;
    this.type = init.type ?? "about:blank";
    this.instance = init.instance ?? "";
    this.requestId = init.requestId ?? null;
    this.retryAfterSeconds = init.retryAfterSeconds ?? null;
    this.invalidParams = init.invalidParams ?? {};
  }
}

/** True when a request was cancelled through its AbortSignal. */
export function isAbortError(err: unknown): boolean {
  return (
    (err instanceof DOMException && err.name === "AbortError") ||
    (err instanceof Error && err.name === "AbortError")
  );
}

function parseRetryAfter(headerValue: string | null): number | null {
  if (!headerValue) return null;
  const seconds = Number(headerValue);
  if (Number.isFinite(seconds) && seconds >= 0) return Math.ceil(seconds);
  const date = Date.parse(headerValue);
  if (Number.isNaN(date)) return null;
  return Math.max(0, Math.ceil((date - Date.now()) / 1000));
}

function fallbackProblem(response: Response): ApiProblemError {
  return new ApiProblemError({
    status: response.status,
    title: response.statusText || "Request failed",
    detail: `The service returned HTTP ${response.status}.`,
    retryAfterSeconds: parseRetryAfter(response.headers.get("Retry-After")),
    requestId: response.headers.get("X-Request-ID"),
  });
}

/** Parse any non-2xx response into an ApiProblemError. */
export async function parseProblem(
  response: Response,
): Promise<ApiProblemError> {
  let body: Partial<ProblemDetails> | null = null;
  try {
    const parsed: unknown = await response.json();
    if (parsed && typeof parsed === "object") {
      body = parsed as Partial<ProblemDetails>;
    }
  } catch {
    return fallbackProblem(response);
  }
  if (!body) return fallbackProblem(response);

  const invalidParams: Record<string, string> = {};
  const rawParams: unknown = body["invalid-params"];
  if (Array.isArray(rawParams)) {
    for (const entry of rawParams) {
      if (
        entry &&
        typeof entry === "object" &&
        typeof (entry as { name?: unknown }).name === "string" &&
        typeof (entry as { reason?: unknown }).reason === "string"
      ) {
        const { name, reason } = entry as { name: string; reason: string };
        // Pydantic locations arrive as `body.username`; bind by the final segment.
        const field = name.split(".").pop() ?? name;
        invalidParams[field] = reason;
      }
    }
  }

  return new ApiProblemError({
    status: typeof body.status === "number" ? body.status : response.status,
    title: body.title ?? (response.statusText || "Request failed"),
    detail: body.detail ?? `The service returned HTTP ${response.status}.`,
    type: body.type,
    instance: body.instance,
    requestId: body.request_id ?? response.headers.get("X-Request-ID"),
    retryAfterSeconds: parseRetryAfter(response.headers.get("Retry-After")),
    invalidParams,
  });
}

interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  query?: Record<string, string | number | boolean | null | undefined>;
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

async function send(path: string, options: RequestOptions): Promise<Response> {
  const url = new URL(`${API_BASE}${path}`, window.location.origin);
  for (const [key, value] of Object.entries(options.query ?? {})) {
    if (value !== null && value !== undefined && value !== "") {
      url.searchParams.set(key, String(value));
    }
  }
  const headers: Record<string, string> = { ...options.headers };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";

  let response: Response;
  try {
    response = await fetch(url.toString(), {
      method: options.method ?? "GET",
      headers,
      credentials: "include",
      body:
        options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
    });
  } catch (err) {
    if (isAbortError(err)) throw err;
    throw new ApiProblemError({
      status: 0,
      title: "Service unreachable",
      detail:
        "Could not reach the service. Check your connection, then try again.",
      cause: err,
    });
  }
  if (!response.ok) throw await parseProblem(response);
  return response;
}

async function requestJson<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const response = await send(path, options);
  return (await response.json()) as T;
}

async function requestVoid(
  path: string,
  options: RequestOptions = {},
): Promise<void> {
  await send(path, options);
}

async function requestFile(
  path: string,
  options: RequestOptions,
): Promise<DownloadedFile> {
  const response = await send(path, options);
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const match = /filename="?([^";]+)"?/.exec(disposition);
  return { blob: await response.blob(), filename: match?.[1] ?? null };
}

type Opt = { signal?: AbortSignal };

function newIdempotencyKey(): string {
  return crypto.randomUUID();
}

export const api = {
  auth: {
    status: (o: Opt = {}) =>
      requestJson<AuthStatusResponse>("/api/auth/status", o),
    login: (body: LoginRequest, o: Opt = {}) =>
      requestJson<AuthenticatedUser>("/api/auth/login", {
        method: "POST",
        body,
        ...o,
      }),
    async register(
      body: RegisterRequest,
      o: Opt = {},
    ): Promise<RegisterOutcome> {
      const response = await send("/api/auth/register", {
        method: "POST",
        body,
        ...o,
      });
      const status = (await response.json()) as AuthStatusResponse;
      return {
        httpStatus: response.status,
        pendingApproval: response.status === 202,
        status,
      };
    },
    logout: (o: Opt = {}) =>
      requestVoid("/api/auth/logout", { method: "POST", ...o }),
    changePassword: (body: ChangePasswordRequest, o: Opt = {}) =>
      requestJson<AuthenticatedUser>("/api/auth/change-password", {
        method: "POST",
        body,
        ...o,
      }),
  },

  async generate(
    request: TeacherRequest,
    o: Opt & { idempotencyKey?: string } = {},
  ): Promise<GenerateResult> {
    const response = await send("/api/generate", {
      method: "POST",
      body: request,
      headers: { "Idempotency-Key": o.idempotencyKey ?? newIdempotencyKey() },
      signal: o.signal,
    });
    return {
      body: (await response.json()) as unknown,
      generationUuid: response.headers.get("X-Generation-UUID"),
      cacheHit: response.headers.get("X-Cache-Hit") === "true",
    };
  },

  export: {
    adHoc: (fmt: string, content: GeneratedContent, o: Opt = {}) =>
      requestFile(`/api/export/${encodeURIComponent(fmt)}`, {
        method: "POST",
        body: content,
        ...o,
      }),
    savedLesson: (uuid: string, fmt: string, o: Opt = {}) =>
      requestFile(
        `/api/lessons/${encodeURIComponent(uuid)}/export/${encodeURIComponent(fmt)}`,
        { method: "POST", ...o },
      ),
  },

  lessons: {
    list: (
      q: {
        cursor?: string | null;
        limit?: number;
        subject?: string;
        task_type?: TaskType | "";
      },
      o: Opt = {},
    ) => requestJson<SavedLessonPage>("/api/lessons", { query: q, ...o }),
    get: (uuid: string, o: Opt = {}) =>
      requestJson<SavedLessonDetail>(
        `/api/lessons/${encodeURIComponent(uuid)}`,
        o,
      ),
    update: (uuid: string, body: SavedLessonUpdate, o: Opt = {}) =>
      requestJson<SavedLessonDetail>(
        `/api/lessons/${encodeURIComponent(uuid)}`,
        {
          method: "PATCH",
          body,
          ...o,
        },
      ),
    remove: (uuid: string, o: Opt = {}) =>
      requestVoid(`/api/lessons/${encodeURIComponent(uuid)}`, {
        method: "DELETE",
        ...o,
      }),
  },

  admin: {
    overview: (o: Opt = {}) =>
      requestJson<AdminOverviewMetrics>("/api/admin/overview", o),
    users: (
      q: { cursor?: string | null; limit?: number; status?: string },
      o: Opt = {},
    ) => requestJson<AdminUserPage>("/api/admin/users", { query: q, ...o }),
    updateUser: (uuid: string, body: AdminUserUpdateRequest, o: Opt = {}) =>
      requestJson<AuthenticatedUser>(
        `/api/admin/users/${encodeURIComponent(uuid)}`,
        {
          method: "PATCH",
          body,
          ...o,
        },
      ),
    resetPassword: (uuid: string, o: Opt = {}) =>
      requestJson<AdminPasswordResetResponse>(
        `/api/admin/users/${encodeURIComponent(uuid)}/reset-password`,
        { method: "POST", ...o },
      ),
    revokeSessions: (uuid: string, o: Opt = {}) =>
      requestJson<SessionRevokeResponse>(
        `/api/admin/users/${encodeURIComponent(uuid)}/revoke-sessions`,
        { method: "POST", ...o },
      ),
    inviteCodes: (q: { cursor?: string | null; limit?: number }, o: Opt = {}) =>
      requestJson<InviteCodePage>("/api/admin/invite-codes", {
        query: q,
        ...o,
      }),
    createInviteCode: (body: InviteCodeCreate, o: Opt = {}) =>
      requestJson<InviteCodeRecord>("/api/admin/invite-codes", {
        method: "POST",
        body,
        ...o,
      }),
    deactivateInviteCode: (uuid: string, o: Opt = {}) =>
      requestVoid(`/api/admin/invite-codes/${encodeURIComponent(uuid)}`, {
        method: "DELETE",
        ...o,
      }),
    settings: (o: Opt = {}) =>
      requestJson<SystemSettings>("/api/admin/settings", o),
    updateSettings: (body: Omit<SystemSettings, "updated_at">, o: Opt = {}) =>
      requestJson<SystemSettings>("/api/admin/settings", {
        method: "PUT",
        body,
        ...o,
      }),
    auditEvents: (
      q: { cursor?: string | null; limit?: number; event_type?: string },
      o: Opt = {},
    ) =>
      requestJson<AuditEventPage>("/api/admin/audit-events", {
        query: q,
        ...o,
      }),
    tokenLedger: (q: { cursor?: string | null; limit?: number }, o: Opt = {}) =>
      requestJson<TokenLedgerPage>("/api/admin/token-ledger", {
        query: q,
        ...o,
      }),
  },
};

export type Api = typeof api;
