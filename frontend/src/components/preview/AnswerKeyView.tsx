import { ShieldCheck } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { MdField } from "@/components/preview/fields";
import { Md, Mdi } from "@/components/preview/Markdown";
import type { GeneratedContent, Lesson } from "@/types/content";

const has = (v: string | null | undefined): v is string =>
  Boolean(v && v.trim());

function AnswerBlock({ children }: { children: React.ReactNode }) {
  return (
    <div className="question-block rounded-md border border-border border-l-4 border-l-success bg-card p-3">
      {children}
    </div>
  );
}

function Heading({ children }: { children: React.ReactNode }) {
  return (
    <h4 className="m-0 mt-1 border-b border-border pb-1 text-[11px] font-bold uppercase tracking-wider text-primary">
      {children}
    </h4>
  );
}

function TierAnswers({
  label,
  answers,
}: {
  label: string;
  answers: string[] | null | undefined;
}) {
  const list = answers?.filter(has) ?? [];
  if (list.length === 0) return null;
  return (
    <AnswerBlock>
      <Badge
        variant={
          label === "Stretch"
            ? "solid"
            : label === "Core"
              ? "default"
              : "neutral"
        }
      >
        {label}
      </Badge>
      <ol className="m-0 mt-2 list-decimal pl-5 text-[13px]">
        {list.map((a, i) => (
          <li key={i}>
            <Md>{a}</Md>
          </li>
        ))}
      </ol>
    </AnswerBlock>
  );
}

function LessonSolutions({ lesson, index }: { lesson: Lesson; index: number }) {
  const base = ["lessons", index] as const;
  const din = lesson.din?.questions.filter((q) => has(q.answer_md)) ?? [];
  const weDo = lesson.we_do?.filter((t) => has(t.worked_answer_md)) ?? [];
  const exam = lesson.exam_questions?.filter((q) => has(q.answer_md)) ?? [];
  const exit = lesson.exit_ticket_answers?.filter(has) ?? [];
  const you = lesson.you_do;
  const youHas =
    Boolean(you?.foundation_answers?.filter(has).length) ||
    Boolean(you?.core_answers?.filter(has).length) ||
    Boolean(you?.stretch_answers?.filter(has).length);
  if (!din.length && !weDo.length && !youHas && !exam.length && !exit.length)
    return null;

  return (
    <article
      className="flex flex-col gap-3"
      aria-label={`Lesson ${index + 1} solutions`}
    >
      <h3 className="m-0 border-b-2 border-primary pb-1 text-base font-bold">
        <Mdi>{lesson.title}</Mdi>
      </h3>

      {din.length > 0 && (
        <section className="flex flex-col gap-2">
          <Heading>Do It Now</Heading>
          {din.map((q) => {
            const qi = lesson.din!.questions.indexOf(q);
            return (
              <AnswerBlock key={q.number}>
                <div className="flex gap-2 text-[13px]">
                  <span className="tabular-nums font-bold text-muted-foreground">
                    Q{q.number}
                  </span>
                  <div className="min-w-0 flex-1">
                    <Md className="text-muted-foreground">{q.prompt_md}</Md>
                    <MdField
                      path={[...base, "din", "questions", qi, "answer_md"]}
                      value={q.answer_md ?? ""}
                      label={`Do It Now question ${q.number} answer`}
                    />
                  </div>
                </div>
              </AnswerBlock>
            );
          })}
        </section>
      )}

      {weDo.length > 0 && (
        <section className="flex flex-col gap-2">
          <Heading>We Do: worked solutions</Heading>
          {weDo.map((t) => {
            const ti = lesson.we_do!.indexOf(t);
            return (
              <AnswerBlock key={ti}>
                <Md className="text-muted-foreground">{t.prompt_md}</Md>
                <MdField
                  path={[...base, "we_do", ti, "worked_answer_md"]}
                  value={t.worked_answer_md ?? ""}
                  label={`We Do task ${ti + 1} solution`}
                />
              </AnswerBlock>
            );
          })}
        </section>
      )}

      {youHas && (
        <section className="flex flex-col gap-2">
          <Heading>You Do: differentiated answers</Heading>
          <TierAnswers label="Foundation" answers={you?.foundation_answers} />
          <TierAnswers label="Core" answers={you?.core_answers} />
          <TierAnswers label="Stretch" answers={you?.stretch_answers} />
        </section>
      )}

      {exam.length > 0 && (
        <section className="flex flex-col gap-2">
          <Heading>Exam practice: mark scheme</Heading>
          {exam.map((q) => {
            const qi = lesson.exam_questions!.indexOf(q);
            return (
              <AnswerBlock key={qi}>
                <div className="flex items-start gap-3 text-[13px]">
                  <span className="tabular-nums font-bold text-muted-foreground">
                    {qi + 1}.
                  </span>
                  <Md className="min-w-0 flex-1 text-muted-foreground">
                    {q.prompt_md}
                  </Md>
                  <span className="tabular-nums shrink-0 font-bold text-primary">
                    [{q.marks} mark{q.marks === 1 ? "" : "s"}]
                  </span>
                </div>
                <div className="mt-1 border-t border-border pt-2">
                  <MdField
                    path={[...base, "exam_questions", qi, "answer_md"]}
                    value={q.answer_md ?? ""}
                    label={`Exam question ${qi + 1} mark scheme`}
                  />
                </div>
              </AnswerBlock>
            );
          })}
        </section>
      )}

      {exit.length > 0 && (
        <section className="flex flex-col gap-2">
          <Heading>Exit ticket answers</Heading>
          <AnswerBlock>
            <ol className="m-0 list-decimal pl-5 text-[13px]">
              {(lesson.exit_ticket_answers ?? []).map((a, i) =>
                has(a) ? (
                  <li key={i}>
                    <MdField
                      path={[...base, "exit_ticket_answers", i]}
                      value={a}
                      label={`Exit ticket answer ${i + 1}`}
                    />
                  </li>
                ) : null,
              )}
            </ol>
          </AnswerBlock>
        </section>
      )}
    </article>
  );
}

/** Complete Teacher Mark Scheme and Solutions view (REQ-SEC-08 / VULN-08). */
export function AnswerKeyView({ content }: { content: GeneratedContent }) {
  const general = content.teacher_answer_key ?? [];
  return (
    <div className="flex flex-col gap-6" data-testid="answer-key-view">
      <div className="flex items-center gap-2 rounded-md bg-primary px-3 py-2 text-xs font-bold uppercase tracking-wider text-primary-foreground">
        <ShieldCheck className="size-4" strokeWidth={1.5} aria-hidden="true" />
        Official mark scheme and teacher solutions
      </div>
      {content.lessons.map((lesson, i) => (
        <LessonSolutions key={i} lesson={lesson} index={i} />
      ))}
      {general.length > 0 && (
        <section className="flex flex-col gap-2">
          <Heading>General teacher answer key and pedagogical notes</Heading>
          {general.map((s, i) => (
            <AnswerBlock key={i}>
              <h5 className="m-0 mb-1 text-[13px] font-bold">{s.heading}</h5>
              <MdField
                path={["teacher_answer_key", i, "markdown_body"]}
                value={s.markdown_body}
                label={`Answer key section ${i + 1}`}
              />
            </AnswerBlock>
          ))}
        </section>
      )}
    </div>
  );
}
