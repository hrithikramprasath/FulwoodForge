import { z } from "zod";
import { ALL_EXAM_BOARDS, ALL_SUBJECTS } from "@/types/content";

/*
 * Runtime validation of a generated resource, RFC 9457 errors, auth payloads,
 * and Admin Control Center DTOs (mirrors backend/app/schemas/*.py).
 */

export const subjectLiteralSchema = z.enum(ALL_SUBJECTS);
export const examBoardLiteralSchema = z.enum(ALL_EXAM_BOARDS);
export const taskTypeSchema = z.enum(["worksheet", "powerpoint", "booklet"]);

const dinQuestion = z.object({
  number: z.number(),
  type: z.enum(["true_false", "mcq", "short"]),
  prompt_md: z.string(),
  options: z.array(z.string()).nullish(),
  answer_md: z.string().nullish(),
});

const spelling = z.object({
  word: z.string(),
  phonetic: z.string(),
  meaning: z.string(),
  dont_confuse: z.string().nullish(),
});

const keyword = z.object({
  term: z.string(),
  tier: z.union([z.literal(2), z.literal(3)]),
  definition: z.string(),
});

const modelStep = z.object({
  label: z.string(),
  body_md: z.string(),
  examiner_tip: z.string().nullish(),
});

const misconception = z.object({
  misconception: z.string(),
  correction: z.string(),
  exam_reference: z.string().nullish(),
});

const task = z.object({
  prompt_md: z.string(),
  scaffold: z.string().nullish(),
  worked_answer_md: z.string().nullish(),
});

const coachPoint = z.object({
  point: z.string(),
  allow: z.string().nullish(),
  do_not_accept: z.string().nullish(),
});

const revisionResource = z.object({
  name: z.string(),
  url: z.string(),
  purpose: z.string(),
});

const examQuestion = z.object({
  prompt_md: z.string(),
  marks: z.number(),
  answer_md: z.string().nullish(),
});

const lesson = z.object({
  title: z.string(),
  lesson_focus: z.string(),
  objectives: z.array(z.string()),
  din: z
    .object({
      recall_focus: z.string(),
      instruction: z.string(),
      questions: z.array(dinQuestion),
    })
    .nullish(),
  key_spellings: z.array(spelling).nullish(),
  keyword_bank: z.array(keyword).nullish(),
  i_do: z.array(modelStep).nullish(),
  misconceptions: z.array(misconception).nullish(),
  we_do: z.array(task).nullish(),
  you_do: z
    .object({
      tasks: z.array(task),
      success_criteria: z.array(z.string()),
      foundation_answers: z.array(z.string()).nullish(),
      core_answers: z.array(z.string()).nullish(),
      stretch_answers: z.array(z.string()).nullish(),
    })
    .nullish(),
  exam_language_coach: z.array(coachPoint).nullish(),
  exam_questions: z.array(examQuestion).nullish(),
  exit_ticket: z.array(z.string()).nullish(),
  exit_ticket_answers: z.array(z.string()).nullish(),
  feedback: z
    .object({
      even_better_if: z.string().nullish(),
      improvement_prompt: z.string(),
    })
    .nullish(),
  revision_resources: z.array(revisionResource).nullish(),
});

export const generatedContentSchema = z.object({
  title: z.string().min(1),
  task_type: taskTypeSchema,
  subject: z.string(),
  year_group: z.string(),
  exam_board: z.string().nullish(),
  spec_reference: z.string().nullish(),
  audience_note: z.string(),
  overview: z.object({
    summary: z.string(),
    objectives: z.array(z.string()),
    assessment_focus: z.string(),
  }),
  lessons: z.array(lesson).min(1),
  teacher_answer_key: z
    .array(z.object({ heading: z.string(), markdown_body: z.string() }))
    .nullish(),
  difficulty_note: z.string(),
  source: z.enum(["gemini", "cache", "fallback", "offline"]).optional(),
});

// ── IETF RFC 9457 Problem Details Schema (REQ-API-01) ────────────────────────

export const invalidParamSchema = z.object({
  name: z.string(),
  reason: z.string(),
});

export const problemDetailsSchema = z.object({
  type: z.string().default("about:blank"),
  title: z.string(),
  status: z.number().int().min(400).max(599),
  detail: z.string(),
  instance: z.string(),
  "invalid-params": z.array(invalidParamSchema).optional(),
  request_id: z.string().optional(),
});

// ── Native Username + Password Auth Schemas (REQ-AUTH-01..06) ────────────────

export const USERNAME_REGEX = /^[a-zA-Z0-9_.-]{3,32}$/;

export const loginRequestSchema = z.object({
  username: z
    .string()
    .trim()
    .regex(
      USERNAME_REGEX,
      "Username must be 3-32 characters (letters, numbers, ., _, -)",
    ),
  password: z.string().min(1, "Enter your password").max(128),
});

export const registerRequestSchema = z.object({
  username: z
    .string()
    .trim()
    .regex(
      USERNAME_REGEX,
      "Username must be 3-32 characters (letters, numbers, ., _, -); no email addresses",
    ),
  display_name: z.string().max(64).optional(),
  password: z
    .string()
    .min(10, "Password must be at least 10 characters")
    .max(128, "Password must be at most 128 characters"),
  invite_code: z.string().max(32).optional().nullable(),
});

export const changePasswordRequestSchema = z
  .object({
    current_password: z.string().min(1, "Enter your current password").max(128),
    new_password: z
      .string()
      .min(10, "New password must be at least 10 characters")
      .max(128),
  })
  .refine((data) => data.new_password !== data.current_password, {
    path: ["new_password"],
    message: "New password must differ from your current password",
  });

export const authenticatedUserSchema = z.object({
  uuid: z.string(),
  username: z.string(),
  display_name: z.string(),
  role: z.enum(["USER", "ADMIN"]),
  status: z.enum(["ACTIVE", "PENDING", "SUSPENDED"]),
  must_change_password: z.boolean(),
  hourly_generation_limit: z.number().int().nonnegative(),
  created_at: z.string(),
  last_login_at: z.string().nullish(),
});

export const authStatusResponseSchema = z.object({
  authenticated: z.boolean(),
  user: authenticatedUserSchema.nullish(),
  registration_mode: z.enum(["INVITE_ONLY", "ADMIN_APPROVAL", "OPEN"]),
  maintenance_mode: z.boolean(),
  allowed_subjects: z.array(subjectLiteralSchema),
  allowed_task_types: z.array(taskTypeSchema),
});

// ── Saved Lesson Summary & Admin Control Center Schemas ─────────────────────

export const savedLessonSummarySchema = z.object({
  uuid: z.string(),
  title: z.string(),
  task_type: taskTypeSchema,
  subject: z.string(),
  year_group: z.string(),
  exam_board: z.string().nullish(),
  topic: z.string(),
  slide_count: z.number().int().nonnegative(),
  is_archived: z.boolean(),
  created_at: z.string(),
  updated_at: z.string(),
});

export const adminPasswordResetResponseSchema = z.object({
  user_uuid: z.string(),
  username: z.string(),
  temporary_password: z.string(),
  must_change_password: z.boolean(),
  sessions_revoked: z.number().int().nonnegative(),
});

export const inviteCodeRecordSchema = z.object({
  uuid: z.string(),
  code: z.string(),
  max_uses: z.number().int().positive(),
  uses_count: z.number().int().nonnegative(),
  expires_at: z.string().nullish(),
  is_active: z.boolean(),
  created_at: z.string(),
});

export const systemSettingsSchema = z.object({
  registration_mode: z.enum(["INVITE_ONLY", "ADMIN_APPROVAL", "OPEN"]),
  default_hourly_quota: z.number().int().min(1).max(1000),
  maintenance_mode: z.boolean(),
  allowed_subjects: z.array(subjectLiteralSchema).min(1),
  allowed_task_types: z.array(taskTypeSchema).min(1),
  updated_at: z.string().optional(),
});

export const tokenLedgerRecordSchema = z.object({
  uuid: z.string(),
  user_uuid: z.string(),
  username: z.string(),
  generation_uuid: z.string().nullish(),
  model_name: z.string(),
  prompt_tokens: z.number().int().nonnegative(),
  candidate_tokens: z.number().int().nonnegative(),
  total_tokens: z.number().int().nonnegative(),
  estimated_cost_usd: z.string(),
  latency_ms: z.number().int().nonnegative(),
  cache_hit: z.boolean(),
  status: z.enum(["SUCCESS", "CACHE_HIT", "UPSTREAM_ERROR"]),
  created_at: z.string(),
});

export const auditEventRecordSchema = z.object({
  uuid: z.string(),
  actor_username: z.string().nullish(),
  target_username: z.string().nullish(),
  event_type: z.string(),
  ip_address: z.string(),
  metadata: z.record(z.string(), z.unknown()),
  created_at: z.string(),
});

export const adminOverviewMetricsSchema = z.object({
  total_users: z.number().int().nonnegative(),
  active_users: z.number().int().nonnegative(),
  pending_users: z.number().int().nonnegative(),
  active_sessions: z.number().int().nonnegative(),
  generations_24h: z.number().int().nonnegative(),
  cache_hit_rate_24h: z.string(),
  total_tokens_24h: z.number().int().nonnegative(),
  total_cost_usd_24h: z.string(),
  cumulative_cost_usd: z.string(),
});
