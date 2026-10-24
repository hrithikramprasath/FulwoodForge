import type { GeneratedContent, Lesson } from "@/types/content";

const present = (value: string | null | undefined): boolean =>
  Boolean(value && value.trim());

/** Number of answer items of a lesson, across every answer-bearing field. */
export function countLessonAnswers(lesson: Lesson): number {
  let total = 0;
  total +=
    lesson.din?.questions.filter((q) => present(q.answer_md)).length ?? 0;
  total += lesson.we_do?.filter((t) => present(t.worked_answer_md)).length ?? 0;
  total += lesson.you_do?.foundation_answers?.filter(present).length ?? 0;
  total += lesson.you_do?.core_answers?.filter(present).length ?? 0;
  total += lesson.you_do?.stretch_answers?.filter(present).length ?? 0;
  total +=
    lesson.exam_questions?.filter((q) => present(q.answer_md)).length ?? 0;
  total += lesson.exit_ticket_answers?.filter(present).length ?? 0;
  return total;
}

/** Total answered items shown on the Answer Key tab badge. */
export function countAnswers(content: GeneratedContent): number {
  const general = content.teacher_answer_key?.length ?? 0;
  return (
    general + content.lessons.reduce((sum, l) => sum + countLessonAnswers(l), 0)
  );
}

/** True when ANY answer exists (teacher_answer_key OR per-question answers). */
export function hasAnswers(content: GeneratedContent): boolean {
  return countAnswers(content) > 0;
}
