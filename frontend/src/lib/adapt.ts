import type { Ability, TaskType } from "@/types/content";

/*
 * The adaptive form's "brain": everything that changes based on earlier
 * answers lives here as data, so the form component stays readable and new
 * adaptive behavior is added by editing config, not JSX.
 */

export const SUBJECTS = [
  "Maths",
  "English",
  "Science",
  "History",
  "Geography",
  "Computing",
] as const;

export const YEAR_GROUPS = ["7", "8", "9", "10", "11"] as const;

/** GCSE years — the exam-board question only appears for these. */
export const GCSE_YEARS = ["10", "11"];

export function isGcseYear(year: string | undefined): boolean {
  return year !== undefined && GCSE_YEARS.includes(year);
}

/** Exam boards offered for GCSE resources — drives mark-scheme conventions. */
export const EXAM_BOARDS = [
  "AQA",
  "Edexcel",
  "OCR",
  "WJEC / Eduqas",
  "Not sure / general",
] as const;

export const TASK_LABELS: Record<TaskType, string> = {
  worksheet: "Worksheet",
  powerpoint: "PowerPoint",
  booklet: "Booklet",
};

export const ABILITY_LABELS: Record<Ability, string> = {
  foundation: "Foundation",
  mixed: "Mixed",
  high: "High",
};

/** Topic placeholder adapts to the chosen subject — a small visible "it's listening" cue. */
export const TOPIC_PLACEHOLDERS: Record<string, string> = {
  Maths: "e.g. fractions, or solving equations",
  English: "e.g. war poetry, or persuasive writing",
  Science: "e.g. photosynthesis, or cells",
  History: "e.g. the Norman Conquest, or the Cold War",
  Geography: "e.g. rivers and flooding, or urbanisation",
  Computing: "e.g. algorithms, or binary numbers",
};

/** The task-specific "extent" question — the clearest adaptation in the form. */
export interface ExtentField {
  /** Which snake_case form field this writes to. */
  name: "question_count" | "slide_count" | "booklet_length";
  question: string;
  options: { value: string; label: string }[];
}

export const EXTENT_FIELDS: Record<TaskType, ExtentField> = {
  worksheet: {
    name: "question_count",
    question: "How many questions?",
    options: [
      { value: "5", label: "5 — quick starter" },
      { value: "10", label: "10 — standard" },
      { value: "15", label: "15 — full lesson" },
      { value: "20", label: "20 — extended practice" },
    ],
  },
  // Sizes start at 15, not 6. Every Fulwood spine section becomes at least one slide,
  // each I DO step gets its own, and each answer reveal adds another — the measured floor
  // for a complete deck is 14 slides (16 with answers). The old 6/8/10/12 options were
  // impossible to honour: live runs returned 20/20/18/19 whatever was picked. These
  // numbers are what the deck can actually deliver, and gemini.py's _slide_budget()
  // scales the CONTENT to match, which is the only lever that moves the count.
  powerpoint: {
    name: "slide_count",
    question: "How many slides?",
    options: [
      { value: "16", label: "Compact — about 16 slides" },
      { value: "19", label: "Standard — about 19 slides" },
      { value: "21", label: "In depth — about 21 slides" },
    ],
  },
  booklet: {
    name: "booklet_length",
    question: "How long should the booklet be?",
    options: [
      { value: "short", label: "Short — about 4 pages" },
      { value: "standard", label: "Standard — about 8 pages" },
      { value: "extended", label: "Extended — about 12 pages" },
    ],
  },
};

/** Which download formats suit which task type (see MVP_WALKTHROUGH.md §4). */
// Downloads offered per task type (D3): Worksheet/Booklet → PDF + DOCX;
// PowerPoint → PPTX + PDF handout. `.md` dropped (thin, divergent, raw $…$).
export const FORMAT_AVAILABILITY: Record<TaskType, Record<string, boolean>> = {
  worksheet: { docx: true, pptx: false, pdf: true },
  powerpoint: { docx: false, pptx: true, pdf: true },
  booklet: { docx: true, pptx: false, pdf: true },
};

/** Teacher answer key — a SEPARATE download so the student copy can be printed
 *  and handed out as-is. Only offered when the teacher asked for answers. */
export const ANSWER_FORMATS = ["answers-docx", "answers-pdf"] as const;
