/*
 * Shared content, authentication, admin & RFC 9457 error types — Fulwood Forge contract.
 *
 * MIRRORS backend/app/schemas/{content,auth,admin,problem_details}.py.
 * Field names are snake_case on purpose so backend JSON deserialises into
 * these exact shapes with zero renaming.
 */

export type TaskType = "worksheet" | "powerpoint" | "booklet";
export type Ability = "foundation" | "mixed" | "high";
export type YearGroup = "7" | "8" | "9" | "10" | "11";
export type BookletLength = "short" | "standard" | "extended";

export const ALL_SUBJECTS = [
  "Mathematics",
  "English Language",
  "English Literature",
  "Biology",
  "Chemistry",
  "Physics",
  "Combined Science",
  "Computer Science",
  "History",
  "Geography",
  "Religious Studies",
  "French",
  "Spanish",
  "Design and Technology",
  "Art and Design",
  "Music",
  "Physical Education",
  "Business Studies",
  "Creative iMedia",
  "Maths",
  "English",
  "Science",
  "Computing",
] as const;

export type SubjectLiteral = (typeof ALL_SUBJECTS)[number];

export const ALL_EXAM_BOARDS = [
  "AQA",
  "Edexcel",
  "OCR",
  "WJEC",
  "Eduqas",
  "None / General",
  "WJEC / Eduqas",
  "Not sure / general",
] as const;

export type ExamBoardLiteral = (typeof ALL_EXAM_BOARDS)[number];

/** Everything the adaptive form collects from the teacher. */
export interface TeacherRequest {
  task_type: TaskType;
  subject: SubjectLiteral | string;
  year_group: YearGroup | string;
  topic: string;
  ability: Ability;
  question_count?: string;
  slide_count?: string;
  booklet_length?: BookletLength | string;
  exam_board?: ExamBoardLiteral | string;
  include_answer_key?: boolean;
}

export type GenerateRequest = TeacherRequest;

// ── Lesson parts (the Fulwood spine) ─────────────────────────────────────────

export interface DinQuestion {
  number: number;
  type: "true_false" | "mcq" | "short";
  prompt_md: string;
  /** Exactly 4 for an mcq; absent otherwise. */
  options?: string[] | null;
  /** Answer — only when the teacher requested an answer key. */
  answer_md?: string | null;
}

export interface DinTask {
  recall_focus: string;
  instruction: string;
  questions: DinQuestion[];
}

export interface Spelling {
  word: string;
  phonetic: string;
  meaning: string;
  dont_confuse?: string | null;
}

export interface Keyword {
  term: string;
  tier: 2 | 3;
  definition: string;
}

export interface ModelStep {
  label: string;
  body_md: string;
  examiner_tip?: string | null;
}

export interface Misconception {
  misconception: string;
  correction: string;
  exam_reference?: string | null;
}

export interface Task {
  prompt_md: string;
  scaffold?: string | null;
  /** Worked solution for a We Do task. Optional: rendered only when the API supplies it. */
  worked_answer_md?: string | null;
}

export interface YouDo {
  tasks: Task[];
  success_criteria: string[];
  /** Differentiated tier answers. Optional: rendered only when the API supplies them. */
  foundation_answers?: string[] | null;
  core_answers?: string[] | null;
  stretch_answers?: string[] | null;
}

export interface CoachPoint {
  point: string;
  allow?: string | null;
  do_not_accept?: string | null;
}

export interface ExamQuestion {
  prompt_md: string;
  marks: number;
  /** Worked answer / mark scheme — only when the teacher requested an answer key. */
  answer_md?: string | null;
}

export interface Feedback {
  even_better_if?: string | null;
  improvement_prompt: string;
}

/** A free revision website for home study — the PowerPoint's closing slide. */
export interface RevisionResource {
  name: string;
  url: string;
  purpose: string;
}

/** One Fulwood lesson. Everything after objectives is optional so the same shape
 * serves a compact worksheet, a slide deck, or a full booklet lesson. */
export interface Lesson {
  title: string;
  lesson_focus: string;
  objectives: string[];
  din?: DinTask | null;
  key_spellings?: Spelling[] | null;
  keyword_bank?: Keyword[] | null;
  i_do?: ModelStep[] | null;
  misconceptions?: Misconception[] | null;
  we_do?: Task[] | null;
  you_do?: YouDo | null;
  exam_language_coach?: CoachPoint[] | null;
  exam_questions?: ExamQuestion[] | null;
  exit_ticket?: string[] | null;
  /** Answers to exit_ticket, same order/length — only when requested. */
  exit_ticket_answers?: string[] | null;
  feedback?: Feedback | null;
  /** Home-revision websites — used instead of feedback in a PowerPoint. */
  revision_resources?: RevisionResource[] | null;
}

/** The teacher-facing "at a glance" digest shown above the resource. */
export interface Overview {
  summary: string;
  objectives: string[];
  assessment_focus: string;
}

/** A teacher answer-key section — only present when the teacher requested it. */
export interface AnswerSection {
  heading: string;
  markdown_body: string;
}

/** The full generated resource — Gemini structured JSON. */
export interface GeneratedContent {
  title: string;
  task_type: TaskType;
  subject: string;
  year_group: string;
  exam_board?: string | null;
  spec_reference?: string | null;
  audience_note: string;
  overview: Overview;
  lessons: Lesson[];
  teacher_answer_key?: AnswerSection[] | null;
  difficulty_note: string;
  /**
   * Where the content came from. Absent/"gemini" = live AI. "cache" = repeat
   * request. "fallback" = legacy stub. "offline" = frontend sample.
   */
  source?: "gemini" | "cache" | "fallback" | "offline";
}

// ── IETF RFC 9457 Problem Details (REQ-API-01) ───────────────────────────────

export interface InvalidParam {
  name: string;
  reason: string;
}

export interface ProblemDetails {
  type: string;
  title: string;
  status: number;
  detail: string;
  instance: string;
  "invalid-params"?: InvalidParam[];
  request_id?: string;
}

// ── Native Username + Password Auth & Session Types (REQ-AUTH-01..06) ────────

export type UserRole = "USER" | "ADMIN";
export type UserStatus = "ACTIVE" | "PENDING" | "SUSPENDED";
export type RegistrationMode = "INVITE_ONLY" | "ADMIN_APPROVAL" | "OPEN";

export interface LoginRequest {
  username: string;
  password: string;
}

export interface RegisterRequest {
  username: string;
  display_name?: string;
  password: string;
  invite_code?: string | null;
}

export interface ChangePasswordRequest {
  current_password: string;
  new_password: string;
}

export interface AuthenticatedUser {
  uuid: string;
  username: string;
  display_name: string;
  role: UserRole;
  status: UserStatus;
  must_change_password: boolean;
  hourly_generation_limit: number;
  created_at: string;
  last_login_at?: string | null;
}

export interface AuthStatusResponse {
  authenticated: boolean;
  user?: AuthenticatedUser | null;
  registration_mode: RegistrationMode;
  maintenance_mode: boolean;
  allowed_subjects: SubjectLiteral[];
  allowed_task_types: TaskType[];
}

// ── Saved Lesson Library & Admin Control Center Types (REQ-ADMIN-01..02) ─────

export interface SavedLessonSummary {
  uuid: string;
  title: string;
  task_type: TaskType;
  subject: SubjectLiteral | string;
  year_group: YearGroup | string;
  tier?: string | null;
  exam_board?: ExamBoardLiteral | string | null;
  topic: string;
  slide_count: number;
  is_archived: boolean;
  created_at: string;
  updated_at: string;
}

export type AdminUserRecord = AuthenticatedUser;

export interface AdminUserUpdateRequest {
  role?: UserRole;
  status?: UserStatus;
  hourly_generation_limit?: number;
}

export interface AdminPasswordResetResponse {
  user_uuid: string;
  username: string;
  temporary_password: string;
  must_change_password: boolean;
  sessions_revoked: number;
}

export interface InviteCodeRecord {
  uuid: string;
  code: string;
  max_uses: number;
  uses_count: number;
  expires_at?: string | null;
  is_active: boolean;
  created_at: string;
}

export interface SystemSettings {
  registration_mode: RegistrationMode;
  default_hourly_quota: number;
  maintenance_mode: boolean;
  allowed_subjects: SubjectLiteral[];
  allowed_task_types: TaskType[];
  updated_at?: string;
}

export interface TokenLedgerRecord {
  uuid: string;
  user_uuid: string;
  username: string;
  generation_uuid?: string | null;
  model_name: string;
  prompt_tokens: number;
  candidate_tokens: number;
  total_tokens: number;
  estimated_cost_usd: string;
  latency_ms: number;
  cache_hit: boolean;
  status: "SUCCESS" | "CACHE_HIT" | "UPSTREAM_ERROR";
  created_at: string;
}

export interface AuditEventRecord {
  uuid: string;
  actor_username?: string | null;
  target_username?: string | null;
  event_type: string;
  ip_address: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface AdminOverviewMetrics {
  total_users: number;
  active_users: number;
  pending_users: number;
  active_sessions: number;
  generations_24h: number;
  cache_hit_rate_24h: string;
  total_tokens_24h: number;
  total_cost_usd_24h: string;
  cumulative_cost_usd: string;
}
