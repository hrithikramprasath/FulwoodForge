import { z } from "zod";
import { ALL_EXAM_BOARDS, ALL_SUBJECTS } from "@/types/content";

const VALID_SUBJECTS = new Set<string>(ALL_SUBJECTS);
const VALID_EXAM_BOARDS = new Set<string>(ALL_EXAM_BOARDS);

/*
 * Zod schema for the adaptive form. Mirrors the TeacherRequest / GenerateRequest
 * contract in backend/app/schemas/content.py (REQ-SEC-09 / VULN-09).
 */
export const teacherRequestSchema = z
  .object({
    task_type: z.enum(["worksheet", "powerpoint", "booklet"]),
    subject: z
      .string()
      .min(1, "Pick a subject")
      .refine(
        (val) => VALID_SUBJECTS.has(val),
        "Choose a supported curriculum subject",
      ),
    year_group: z.enum(["7", "8", "9", "10", "11"]),
    topic: z
      .string()
      .min(2, "Tell us the topic in a few words")
      .max(120, "Keep the topic under 120 characters"),
    ability: z.enum(["foundation", "mixed", "high"]),
    question_count: z.string().optional(),
    slide_count: z.string().optional(),
    booklet_length: z.string().optional(),
    exam_board: z
      .string()
      .optional()
      .refine(
        (val) => !val || VALID_EXAM_BOARDS.has(val),
        "Choose a supported exam board",
      ),
    include_answer_key: z.boolean().optional(),
  })
  // The task-specific field is required for its own task type only.
  .superRefine((data, ctx) => {
    // Exam board is required for GCSE years (10–11), skipped for KS3.
    if (
      (data.year_group === "10" || data.year_group === "11") &&
      !data.exam_board
    ) {
      ctx.addIssue({
        code: "custom",
        path: ["exam_board"],
        message: "Choose an exam board",
      });
    }
    if (data.task_type === "worksheet" && !data.question_count) {
      ctx.addIssue({
        code: "custom",
        path: ["question_count"],
        message: "Choose how many questions",
      });
    }
    if (data.task_type === "powerpoint" && !data.slide_count) {
      ctx.addIssue({
        code: "custom",
        path: ["slide_count"],
        message: "Choose how many slides",
      });
    }
    if (data.task_type === "booklet" && !data.booklet_length) {
      ctx.addIssue({
        code: "custom",
        path: ["booklet_length"],
        message: "Choose a booklet length",
      });
    }
  });

export type TeacherRequestForm = z.infer<typeof teacherRequestSchema>;
