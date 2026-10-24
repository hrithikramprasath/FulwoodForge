import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      auth: {
        status: vi.fn(),
        login: vi.fn(),
        register: vi.fn(),
        logout: vi.fn(),
        changePassword: vi.fn(),
      },
      generate: vi.fn(),
      export: { adHoc: vi.fn(), savedLesson: vi.fn() },
      lessons: {
        list: vi.fn(),
        get: vi.fn(),
        update: vi.fn(),
        remove: vi.fn(),
      },
      admin: {
        overview: vi.fn(),
        users: vi.fn(),
        updateUser: vi.fn(),
        resetPassword: vi.fn(),
        revokeSessions: vi.fn(),
        inviteCodes: vi.fn(),
        createInviteCode: vi.fn(),
        deactivateInviteCode: vi.fn(),
        settings: vi.fn(),
        updateSettings: vi.fn(),
        auditEvents: vi.fn(),
        tokenLedger: vi.fn(),
      },
    },
  };
});

import { AdminDashboard } from "@/components/admin/AdminDashboard";
import { AuthGate } from "@/components/auth/AuthGate";
import { ForcedPasswordChangeModal } from "@/components/auth/ForcedPasswordChangeModal";
import { LoadingState } from "@/components/preview/LoadingState";
import { ResultView } from "@/components/preview/ResultView";
import { api, ApiProblemError } from "@/lib/api";
import type {
  AuthenticatedUser,
  AuthStatusResponse,
  GeneratedContent,
  TeacherRequest,
} from "@/types/content";

const mocked = vi.mocked(api, true);

/** Minimal user-event stand-in built on fireEvent (no extra dependency). */
const userEvent = {
  setup() {
    return {
      async click(el: Element): Promise<void> {
        fireEvent.mouseDown(el);
        fireEvent.mouseUp(el);
        fireEvent.click(el);
      },
      async type(el: Element, text: string): Promise<void> {
        fireEvent.change(el, { target: { value: text } });
      },
    };
  },
};

afterEach(() => {
  vi.clearAllMocks();
});

// ── Fixtures ────────────────────────────────────────────────────────────────

function lessonContent(
  overrides: Partial<GeneratedContent> = {},
): GeneratedContent {
  return {
    title: "Quadratic Formula",
    task_type: "worksheet",
    subject: "Mathematics",
    year_group: "10",
    audience_note: "Year 10",
    overview: {
      summary: "Summary",
      objectives: ["Solve quadratics"],
      assessment_focus: "AO1",
    },
    difficulty_note: "Mixed",
    teacher_answer_key: [],
    lessons: [
      {
        title: "Lesson One",
        lesson_focus: "Focus",
        objectives: ["Objective A"],
        din: {
          recall_focus: "Recall",
          instruction: "Answer these",
          questions: [
            {
              number: 1,
              type: "short",
              prompt_md: "What is $2+2$?",
              answer_md: "DIN-ANSWER-FOUR",
            },
          ],
        },
        exam_questions: [
          {
            prompt_md: "Explain osmosis.",
            marks: 3,
            answer_md: "EXAM-MARK-SCHEME",
          },
        ],
        exit_ticket: ["Exit question one"],
        exit_ticket_answers: ["EXIT-ANSWER-ONE"],
      },
    ],
    ...overrides,
  };
}

const USER: AuthenticatedUser = {
  uuid: "u-1",
  username: "teacher",
  display_name: "Teacher",
  role: "USER",
  status: "ACTIVE",
  must_change_password: false,
  hourly_generation_limit: 10,
  created_at: "2026-01-01T00:00:00Z",
};

function authStatus(
  mode: AuthStatusResponse["registration_mode"],
): AuthStatusResponse {
  return {
    authenticated: false,
    user: null,
    registration_mode: mode,
    maintenance_mode: false,
    allowed_subjects: ["Mathematics"],
    allowed_task_types: ["worksheet"],
  };
}

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    if (name === "node_modules" || name === "__tests__") continue;
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) walk(full, out);
    else out.push(full);
  }
  return out;
}

const FRONTEND = path.resolve(__dirname, "../..");
const BACKEND_APP = path.resolve(FRONTEND, "../backend/app");

// ── Anti-Vibe and security source scan ──────────────────────────────────────

describe("source scan", () => {
  const sources = [
    ...walk(path.join(FRONTEND, "src")),
    path.join(FRONTEND, "renderer/render.mjs"),
    ...walk(BACKEND_APP),
  ].filter((f) => /\.(tsx?|css|mjs|py)$/.test(f));

  it("contains no emoji or MagicSchool purple", () => {
    const emoji = /[\u{1F300}-\u{1FAFF}\u2600-\u27BF]/u;
    const offenders = sources.filter((f) => {
      const text = readFileSync(f, "utf8");
      return emoji.test(text) || /#762bfa/i.test(text);
    });
    expect(offenders).toEqual([]);
  });

  it("routes every network call through lib/api.ts", () => {
    const offenders = sources.filter((f) => {
      const rel = path.relative(path.join(FRONTEND, "src"), f);
      if (rel.startsWith("..")) return false;
      if (!(rel.startsWith("components") || rel === "App.tsx")) return false;
      return /\bfetch\(/.test(readFileSync(f, "utf8"));
    });
    expect(offenders).toEqual([]);
  });

  it("never passes marked output straight to the DOM from components", () => {
    const offenders = sources.filter((f) => {
      const rel = path.relative(path.join(FRONTEND, "src"), f);
      if (!rel.startsWith("components")) return false;
      return /marked\.parse/.test(readFileSync(f, "utf8"));
    });
    expect(offenders).toEqual([]);
  });
});

// ── DOMPurify (VULN-01) ─────────────────────────────────────────────────────

describe("ResultView sanitisation", () => {
  it("strips malicious markup while keeping valid Markdown and maths", () => {
    const content = lessonContent({
      overview: {
        summary:
          "**Bold text** and $x^2$ <img src=x onerror=alert(1)> <script>window.pwned=true</script> [click](javascript:alert(1))",
        objectives: ["Solve quadratics"],
        assessment_focus: "AO1",
      },
    });
    const { container } = render(
      <ResultView
        content={content}
        lessonUuid={null}
        onContentChange={() => {}}
      />,
    );
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("[onerror]")).toBeNull();
    expect((window as unknown as { pwned?: boolean }).pwned).toBeUndefined();
    const links = Array.from(container.querySelectorAll("a"));
    for (const a of links)
      expect(a.getAttribute("href") ?? "").not.toMatch(/^javascript:/i);
    expect(container.querySelector("strong")?.textContent).toBe("Bold text");
    expect(container.querySelector(".katex")).not.toBeNull();
  });
});

// ── Answer key and mark scheme (VULN-08) ────────────────────────────────────

describe("ResultView answer key", () => {
  it("enables the answers tab from per-question answers when teacher_answer_key is empty", async () => {
    const user = userEvent.setup();
    render(
      <ResultView
        content={lessonContent()}
        lessonUuid={null}
        onContentChange={() => {}}
      />,
    );
    const tab = screen.getByRole("radio", { name: /Answer Key & Mark Scheme/ });
    expect(tab).toBeEnabled();
    await user.click(tab);
    expect(screen.getByText(/DIN-ANSWER-FOUR/)).toBeInTheDocument();
    expect(screen.getByText(/EXAM-MARK-SCHEME/)).toBeInTheDocument();
    expect(screen.getByText(/EXIT-ANSWER-ONE/)).toBeInTheDocument();
    expect(screen.getByText(/3 marks/)).toBeInTheDocument();
  });

  it("disables the answers tab when no answers exist", () => {
    const content = lessonContent();
    content.lessons[0].din!.questions[0].answer_md = null;
    content.lessons[0].exam_questions![0].answer_md = null;
    content.lessons[0].exit_ticket_answers = null;
    render(
      <ResultView
        content={content}
        lessonUuid={null}
        onContentChange={() => {}}
      />,
    );
    expect(
      screen.getByRole("radio", { name: /Answer Key & Mark Scheme/ }),
    ).toBeDisabled();
  });
});

// ── Native auth (REQ-AUTH-01..06) ───────────────────────────────────────────

describe("AuthGate", () => {
  it("shows the invite code input only under INVITE_ONLY and never an email field", async () => {
    const user = userEvent.setup();
    render(
      <AuthGate
        status={authStatus("INVITE_ONLY")}
        onAuthenticated={() => {}}
      />,
    );
    await user.click(
      screen.getByRole("tab", { name: /Create Educator Account/ }),
    );
    expect(screen.getByLabelText(/Invite Code/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/email/i)).toBeNull();
  });

  it("omits the invite code under ADMIN_APPROVAL and shows the pending state on 202", async () => {
    const user = userEvent.setup();
    mocked.auth.register.mockResolvedValue({
      httpStatus: 202,
      pendingApproval: true,
      status: authStatus("ADMIN_APPROVAL"),
    });
    render(
      <AuthGate
        status={authStatus("ADMIN_APPROVAL")}
        onAuthenticated={() => {}}
      />,
    );
    await user.click(
      screen.getByRole("tab", { name: /Create Educator Account/ }),
    );
    expect(screen.queryByLabelText(/Invite Code/)).toBeNull();
    await user.type(screen.getByLabelText("Username"), "newteacher");
    await user.type(screen.getByLabelText("Password"), "a-long-password-1");
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(
      await screen.findByText("Account Pending Administrator Approval"),
    ).toBeInTheDocument();
  });

  it("signs in and notifies the shell", async () => {
    const user = userEvent.setup();
    const onAuthenticated = vi.fn();
    mocked.auth.login.mockResolvedValue(USER);
    render(
      <AuthGate
        status={authStatus("OPEN")}
        onAuthenticated={onAuthenticated}
      />,
    );
    await user.type(screen.getByLabelText("Username"), "teacher");
    await user.type(screen.getByLabelText("Password"), "a-long-password-1");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(onAuthenticated).toHaveBeenCalled());
    expect(mocked.auth.login).toHaveBeenCalledWith({
      username: "teacher",
      password: "a-long-password-1",
    });
  });

  it("binds RFC 9457 invalid-params to the username field", async () => {
    const user = userEvent.setup();
    mocked.auth.login.mockRejectedValue(
      new ApiProblemError({
        status: 422,
        title: "Validation failed",
        detail: "Invalid input.",
        invalidParams: { username: "Username must be 3 to 32 characters." },
      }),
    );
    render(<AuthGate status={authStatus("OPEN")} onAuthenticated={() => {}} />);
    await user.type(screen.getByLabelText("Username"), "teacher");
    await user.type(screen.getByLabelText("Password"), "a-long-password-1");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(
      await screen.findByText("Username must be 3 to 32 characters."),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Username")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
  });

  it("shows a tabular countdown on HTTP 429 lockout", async () => {
    const user = userEvent.setup();
    mocked.auth.login.mockRejectedValue(
      new ApiProblemError({
        status: 429,
        title: "Too many attempts",
        detail: "Locked.",
        retryAfterSeconds: 900,
      }),
    );
    const { container } = render(
      <AuthGate status={authStatus("OPEN")} onAuthenticated={() => {}} />,
    );
    await user.type(screen.getByLabelText("Username"), "teacher");
    await user.type(screen.getByLabelText("Password"), "wrong-password-1");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() =>
      expect(container.querySelector(".tabular-nums")).not.toBeNull(),
    );
    expect(await screen.findByText(/15:00|900|14:5\d/)).toBeInTheDocument();
  });
});

describe("ForcedPasswordChangeModal", () => {
  it("is non-dismissible and submits to change-password", async () => {
    const user = userEvent.setup();
    const onChanged = vi.fn();
    mocked.auth.changePassword.mockResolvedValue({
      ...USER,
      must_change_password: false,
    });
    render(<ForcedPasswordChangeModal onChanged={onChanged} />);
    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog).queryByRole("button", { name: /close|cancel/i }),
    ).toBeNull();
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await user.type(
      screen.getByLabelText("Temporary password"),
      "TempPass-12345678",
    );
    await user.type(screen.getByLabelText("New password"), "my-new-password-1");
    await user.type(
      screen.getByLabelText("Confirm new password"),
      "my-new-password-1",
    );
    await user.click(screen.getByRole("button", { name: "Update password" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(mocked.auth.changePassword).toHaveBeenCalledWith({
      current_password: "TempPass-12345678",
      new_password: "my-new-password-1",
    });
  });
});

// ── Phased loading and cancellation (VULN-11) ───────────────────────────────

describe("LoadingState", () => {
  const request: TeacherRequest = {
    task_type: "worksheet",
    subject: "Mathematics",
    year_group: "10",
    topic: "Quadratic formula",
    ability: "mixed",
  };

  it("shows the four stages and wires Cancel Generation to AbortController.abort()", async () => {
    const user = userEvent.setup();
    const controller = new AbortController();
    const abortSpy = vi.spyOn(controller, "abort");
    render(
      <LoadingState request={request} onCancel={() => controller.abort()} />,
    );
    expect(
      screen.getByText("Validating curriculum specification"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Compiling mark schemes & differentiation tiers"),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Elapsed time")).toHaveTextContent(
      /^\d\d:\d\d\.\ds$/,
    );
    await user.click(screen.getByRole("button", { name: "Cancel Generation" }));
    expect(abortSpy).toHaveBeenCalledTimes(1);
    expect(controller.signal.aborted).toBe(true);
  });
});

// ── Admin Control Center ────────────────────────────────────────────────────

describe("AdminDashboard", () => {
  function primeAdminMocks() {
    mocked.admin.overview.mockResolvedValue({
      total_users: 12,
      active_users: 10,
      pending_users: 2,
      active_sessions: 4,
      generations_24h: 33,
      cache_hit_rate_24h: "0.25",
      total_tokens_24h: 123456,
      total_cost_usd_24h: "1.2345",
      cumulative_cost_usd: "9.8765",
    });
    mocked.admin.users.mockResolvedValue({
      items: [
        {
          ...USER,
          uuid: "u-2",
          username: "pending1",
          display_name: "Pending One",
          status: "PENDING",
        },
      ],
      next_cursor: null,
      has_more: false,
    });
    mocked.admin.inviteCodes.mockResolvedValue({
      items: [],
      next_cursor: null,
      has_more: false,
    });
    mocked.admin.auditEvents.mockResolvedValue({
      items: [
        {
          uuid: "a-1",
          actor_username: "admin",
          target_username: null,
          event_type: "LOGIN_SUCCESS",
          ip_address: "127.0.0.1",
          metadata: { k: "v" },
          created_at: "2026-10-17T10:00:00Z",
        },
      ],
      next_cursor: null,
      has_more: false,
    });
    mocked.admin.tokenLedger.mockResolvedValue({
      items: [
        {
          uuid: "t-1",
          user_uuid: "u-2",
          username: "pending1",
          model_name: "gemini-test",
          prompt_tokens: 100,
          candidate_tokens: 200,
          total_tokens: 300,
          estimated_cost_usd: "0.0012",
          latency_ms: 850,
          cache_hit: false,
          status: "SUCCESS",
          created_at: "2026-10-17T10:00:00Z",
        },
      ],
      next_cursor: null,
      has_more: false,
    });
  }

  it("renders telemetry, approves users and issues a one-time password", async () => {
    primeAdminMocks();
    mocked.admin.updateUser.mockResolvedValue({
      ...USER,
      uuid: "u-2",
      username: "pending1",
      status: "ACTIVE",
    });
    mocked.admin.resetPassword.mockResolvedValue({
      user_uuid: "u-2",
      username: "pending1",
      temporary_password: "Abcd1234Efgh5678",
      sessions_revoked: 1,
      must_change_password: true,
    });
    const user = userEvent.setup();
    render(<AdminDashboard currentUserUuid="admin-uuid" />);

    expect(await screen.findByText("Total Educators")).toBeInTheDocument();
    expect(screen.getByText("10 active / 2 pending")).toBeInTheDocument();
    expect(screen.getByText("$1.2345").closest("dd")).toHaveClass(
      "tabular-nums",
    );

    await user.click(
      await screen.findByRole("button", { name: "Approve pending1" }),
    );
    await waitFor(() =>
      expect(mocked.admin.updateUser).toHaveBeenCalledWith("u-2", {
        status: "ACTIVE",
      }),
    );

    await user.click(
      screen.getByRole("button", { name: "Reset password for pending1" }),
    );
    await user.click(
      await screen.findByRole("button", { name: /Issue temporary password/ }),
    );
    expect(await screen.findByTestId("temporary-password")).toHaveTextContent(
      "Abcd1234Efgh5678",
    );
  });

  it("creates invite codes and lists audit and token ledger rows", async () => {
    primeAdminMocks();
    mocked.admin.createInviteCode.mockResolvedValue({
      uuid: "i-1",
      code: "FULWOOD-2026",
      max_uses: 5,
      uses_count: 0,
      expires_at: null,
      is_active: true,
      created_at: "2026-10-17T10:00:00Z",
    });
    const user = userEvent.setup();
    render(<AdminDashboard currentUserUuid="admin-uuid" />);

    await user.click(screen.getByRole("tab", { name: "Invite Codes" }));
    await user.type(
      await screen.findByLabelText(/Code \(blank/),
      "FULWOOD-2026",
    );
    await user.click(screen.getByRole("button", { name: "Create Code" }));
    await waitFor(() =>
      expect(mocked.admin.createInviteCode).toHaveBeenCalledWith(
        expect.objectContaining({ code: "FULWOOD-2026" }),
      ),
    );

    await user.click(screen.getByRole("tab", { name: "Audit Log" }));
    expect(await screen.findByText("LOGIN_SUCCESS")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Token Ledger" }));
    expect(await screen.findByText("gemini-test")).toBeInTheDocument();
    expect(screen.getByText("SUCCESS")).toBeInTheDocument();
    expect(screen.getByText("$0.0012")).toBeInTheDocument();
  });
});

describe("index.html strict CSP font policy (REQ-SEC-01)", () => {
  it("contains zero external Google Fonts links and no external stylesheets", () => {
    const html = readFileSync(
      path.resolve(__dirname, "../../index.html"),
      "utf-8",
    );
    expect(html).not.toContain("fonts.googleapis.com");
    expect(html).not.toContain("fonts.gstatic.com");
    expect(html).not.toMatch(/<link[^>]+href=["']https?:\/\//i);
  });

  it("self-hosts both typefaces via fontsource imports in main.tsx", () => {
    const main = readFileSync(path.resolve(__dirname, "../main.tsx"), "utf-8");
    expect(main).toContain("@fontsource-variable/plus-jakarta-sans");
    expect(main).toContain("@fontsource-variable/jetbrains-mono");
  });
});
