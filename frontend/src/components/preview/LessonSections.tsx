import { useContext } from "react";
import { ChevronDown } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  AnswerReveal,
  MdField,
  SectionsContext,
} from "@/components/preview/fields";
import { Md, Mdi } from "@/components/preview/Markdown";
import type {
  CoachPoint,
  DinTask,
  ExamQuestion,
  Feedback,
  Keyword,
  Lesson,
  Misconception,
  ModelStep,
  RevisionResource,
  Spelling,
  Task,
  YouDo,
} from "@/types/content";

type P = readonly (string | number)[];

/** MCQ option label; mirrors `option_letter()` in backend docx_writer.py. */
const optionLetter = (i: number) =>
  i < 26 ? String.fromCharCode(65 + i) : String(i + 1);

/**
 * Numbered, collapsible exam-paper section with a navy accent bar. `key` carries
 * the expand/collapse-all epoch so the toolbar toggle remounts it with the new state.
 */
export function Part({
  number,
  title,
  meta,
  children,
}: {
  number: number;
  title: string;
  meta?: React.ReactNode;
  children: React.ReactNode;
}) {
  const { allOpen, epoch } = useContext(SectionsContext);
  return (
    <Collapsible key={epoch} defaultOpen={allOpen} asChild>
      <section className="overflow-hidden rounded-md border border-border bg-card">
        <CollapsibleTrigger className="fx-transition group flex w-full items-center gap-3 border-l-4 border-primary bg-surface-subtle px-3 py-2 text-left hover:bg-muted">
          <span className="tabular-nums flex size-6 shrink-0 items-center justify-center rounded bg-primary text-[11px] font-bold text-primary-foreground">
            {number}
          </span>
          <span className="text-[13px] font-bold tracking-tight text-foreground">
            {title}
          </span>
          {meta && (
            <span className="ml-auto min-w-0 text-[11px] font-medium text-muted-foreground">
              {meta}
            </span>
          )}
          <ChevronDown
            className="no-print size-4 shrink-0 text-muted-foreground transition-transform group-data-[state=open]:rotate-180"
            strokeWidth={1.5}
            aria-hidden="true"
          />
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="flex flex-col gap-3 p-4">{children}</div>
        </CollapsibleContent>
      </section>
    </Collapsible>
  );
}

function DinView({ d, base, n }: { d: DinTask; base: P; n: number }) {
  return (
    <Part number={n} title="Do It Now" meta={<Mdi>{d.recall_focus}</Mdi>}>
      <p className="m-0 text-xs italic text-muted-foreground">
        {d.instruction}
      </p>
      <ol className="m-0 flex list-none flex-col gap-3 p-0">
        {d.questions.map((q, qi) => (
          <li key={q.number} className="question-block flex gap-2.5">
            <span className="tabular-nums mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-primary text-[11px] font-bold text-primary-foreground">
              {q.number}
            </span>
            <div className="min-w-0 flex-1">
              <MdField
                path={[...base, "din", "questions", qi, "prompt_md"]}
                value={q.prompt_md}
                label={`Do It Now question ${q.number}`}
              />
              {q.type === "mcq" && q.options && (
                <ol className="m-0 mt-1 grid list-none grid-cols-1 gap-x-4 p-0 text-[13px] sm:grid-cols-2">
                  {q.options.map((o, i) => (
                    <li key={i} className="flex gap-1.5">
                      <span className="font-semibold text-muted-foreground">
                        {optionLetter(i)})
                      </span>
                      <Mdi>{o}</Mdi>
                    </li>
                  ))}
                </ol>
              )}
              {q.type === "true_false" && (
                <p className="m-0 text-xs font-semibold text-muted-foreground">
                  True / False
                </p>
              )}
              <AnswerReveal
                path={[...base, "din", "questions", qi, "answer_md"]}
                answer={q.answer_md}
                label={`Do It Now question ${q.number}`}
              />
            </div>
          </li>
        ))}
      </ol>
    </Part>
  );
}

function SpellingsView({ list, n }: { list: Spelling[]; n: number }) {
  return (
    <Part
      number={n}
      title={`${list.length} Key Spelling${list.length === 1 ? "" : "s"}`}
    >
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {list.map((s, i) => (
          <div
            key={i}
            className="question-block rounded-md border border-border bg-surface-subtle p-3"
          >
            <div className="font-bold text-primary">{s.word}</div>
            <div className="mb-1 text-xs tracking-wide text-muted-foreground">
              {s.phonetic}
            </div>
            <div className="text-[13px] text-text-secondary">
              <Mdi>{s.meaning}</Mdi>
            </div>
            {s.dont_confuse && (
              <div className="mt-1 text-xs text-danger">
                <strong>Do not confuse:</strong> <Mdi>{s.dont_confuse}</Mdi>
              </div>
            )}
          </div>
        ))}
      </div>
    </Part>
  );
}

function KeywordsView({
  list,
  hasBoard,
  n,
}: {
  list: Keyword[];
  hasBoard: boolean;
  n: number;
}) {
  return (
    <Part number={n} title="Keyword Bank">
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-[13px]">
          <thead>
            <tr className="text-left">
              <th className="border border-border bg-muted px-2.5 py-1.5">
                Key term
              </th>
              <th className="border border-border bg-muted px-2.5 py-1.5">
                {hasBoard ? "Definition (mark-scheme wording)" : "Definition"}
              </th>
            </tr>
          </thead>
          <tbody>
            {list.map((k, i) => (
              <tr key={i} className="question-block">
                <td className="whitespace-nowrap border border-border px-2.5 py-1.5 align-top font-semibold">
                  <Mdi>{k.term}</Mdi>{" "}
                  <Badge
                    variant={k.tier === 3 ? "solid" : "default"}
                    className="tabular-nums"
                  >
                    Tier {k.tier}
                  </Badge>
                </td>
                <td className="border border-border px-2.5 py-1.5 align-top">
                  <Md>{k.definition}</Md>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Part>
  );
}

function IDoView({
  steps,
  base,
  n,
}: {
  steps: ModelStep[];
  base: P;
  n: number;
}) {
  return (
    <Part number={n} title="I Do: Watch, listen and annotate">
      <div className="grid grid-cols-1 gap-x-6 gap-y-3 md:grid-cols-[1fr_1fr]">
        <p className="m-0 hidden text-[11px] font-semibold uppercase tracking-wider text-muted-foreground md:block">
          Worked example
        </p>
        <p className="m-0 hidden text-[11px] font-semibold uppercase tracking-wider text-muted-foreground md:block">
          Step-by-step rationale
        </p>
        {steps.map((s, i) => (
          <div key={i} className="question-block contents">
            <div className="rounded-md border border-border bg-surface-subtle p-3">
              <div className="mb-0.5 text-[13px] font-bold text-primary">
                <MdField
                  path={[...base, "i_do", i, "label"]}
                  value={s.label}
                  label={`I Do step ${i + 1} label`}
                  inline
                />
              </div>
              <MdField
                path={[...base, "i_do", i, "body_md"]}
                value={s.body_md}
                label={`I Do step ${i + 1} body`}
              />
            </div>
            <div className="text-[13px] text-text-secondary">
              {s.examiner_tip ? (
                <div className="rounded-r-md border-l-[3px] border-primary bg-accent px-3 py-2">
                  <strong>Examiner tip:</strong>{" "}
                  <MdField
                    path={[...base, "i_do", i, "examiner_tip"]}
                    value={s.examiner_tip}
                    label={`I Do step ${i + 1} examiner tip`}
                    inline
                  />
                </div>
              ) : (
                <span className="text-xs text-muted-foreground">
                  Step {i + 1}
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
    </Part>
  );
}

function MisconceptionsView({ list, n }: { list: Misconception[]; n: number }) {
  return (
    <Part number={n} title="Misconception Check">
      {list.map((m, i) => (
        <div key={i} className="question-block flex flex-col gap-1 text-[13px]">
          <div className="flex gap-2">
            <Badge variant="danger">Misconception</Badge>
            <Mdi>{m.misconception}</Mdi>
          </div>
          <div className="flex gap-2">
            <Badge variant="success">Correct</Badge>
            <Mdi>{m.correction}</Mdi>
          </div>
          {m.exam_reference && (
            <div className="text-xs italic text-muted-foreground">
              {m.exam_reference}
            </div>
          )}
        </div>
      ))}
    </Part>
  );
}

function Tasks({
  tasks,
  path,
  label,
}: {
  tasks: Task[];
  path: P;
  label: string;
}) {
  return (
    <ol className="m-0 flex list-decimal flex-col gap-3 pl-5">
      {tasks.map((t, i) => (
        <li key={i} className="question-block">
          <MdField
            path={[...path, i, "prompt_md"]}
            value={t.prompt_md}
            label={`${label} task ${i + 1}`}
          />
          {t.scaffold && (
            <div className="mt-0.5 text-xs text-primary">
              <strong>Scaffold:</strong> <Mdi>{t.scaffold}</Mdi>
            </div>
          )}
          <AnswerReveal
            path={[...path, i, "worked_answer_md"]}
            answer={t.worked_answer_md}
            label={`${label} task ${i + 1}`}
          />
        </li>
      ))}
    </ol>
  );
}

function WeDoView({ tasks, base, n }: { tasks: Task[]; base: P; n: number }) {
  return (
    <Part number={n} title="We Do: Together">
      <Tasks tasks={tasks} path={[...base, "we_do"]} label="We Do" />
    </Part>
  );
}

function YouDoView({ y, base, n }: { y: YouDo; base: P; n: number }) {
  return (
    <Part
      number={n}
      title="You Do: Independent work"
      meta={
        <span className="flex gap-1">
          <Badge variant="neutral">Foundation</Badge>
          <Badge variant="default">Core</Badge>
          <Badge variant="solid">Stretch</Badge>
        </span>
      }
    >
      {y.success_criteria?.length > 0 && (
        <div className="rounded-md border border-border bg-success-bg/40 px-3 py-2">
          <div className="text-[13px] font-bold text-success">
            Success criteria
          </div>
          <ul className="m-0 mt-1 list-disc pl-5 text-[13px]">
            {y.success_criteria.map((c, i) => (
              <li key={i}>
                <Mdi>{c}</Mdi>
              </li>
            ))}
          </ul>
        </div>
      )}
      <Tasks
        tasks={y.tasks}
        path={[...base, "you_do", "tasks"]}
        label="You Do"
      />
    </Part>
  );
}

function ExamCoachView({ list, n }: { list: CoachPoint[]; n: number }) {
  return (
    <Part number={n} title="Exam Language Coach">
      {list.map((c, i) => (
        <div key={i} className="question-block text-[13px]">
          <div className="font-semibold">
            <Mdi>{c.point}</Mdi>
          </div>
          {c.allow && (
            <div className="text-success">
              <strong>ALLOW:</strong> <Mdi>{c.allow}</Mdi>
            </div>
          )}
          {c.do_not_accept && (
            <div className="text-danger">
              <strong>DO NOT ACCEPT:</strong> <Mdi>{c.do_not_accept}</Mdi>
            </div>
          )}
        </div>
      ))}
    </Part>
  );
}

function ExamQuestionsView({
  list,
  base,
  n,
}: {
  list: ExamQuestion[];
  base: P;
  n: number;
}) {
  return (
    <Part number={n} title="Exam Questions">
      {list.map((q, i) => (
        <div key={i} className="question-block">
          <div className="flex items-start gap-3 text-[13px]">
            <span className="tabular-nums shrink-0 font-bold text-muted-foreground">
              {i + 1}.
            </span>
            <div className="min-w-0 flex-1">
              <MdField
                path={[...base, "exam_questions", i, "prompt_md"]}
                value={q.prompt_md}
                label={`Exam question ${i + 1}`}
              />
            </div>
            <span className="tabular-nums shrink-0 font-bold text-primary">
              [{q.marks} mark{q.marks === 1 ? "" : "s"}]
            </span>
          </div>
          <AnswerReveal
            path={[...base, "exam_questions", i, "answer_md"]}
            answer={q.answer_md}
            label={`exam question ${i + 1}`}
          />
        </div>
      ))}
    </Part>
  );
}

function ExitTicketView({
  list,
  answers,
  base,
  n,
}: {
  list: string[];
  answers: string[] | null | undefined;
  base: P;
  n: number;
}) {
  return (
    <Part number={n} title="Exit Ticket">
      <ol className="m-0 flex list-decimal flex-col gap-2 pl-5 text-[13px]">
        {list.map((q, i) => (
          <li key={i} className="question-block">
            <MdField
              path={[...base, "exit_ticket", i]}
              value={q}
              label={`Exit ticket question ${i + 1}`}
            />
            <AnswerReveal
              path={[...base, "exit_ticket_answers", i]}
              answer={answers?.[i]}
              label={`exit ticket question ${i + 1}`}
            />
          </li>
        ))}
      </ol>
    </Part>
  );
}

function FeedbackView({ f, n }: { f: Feedback; n: number }) {
  return (
    <Part number={n} title="Feedback and Improvement">
      {f.even_better_if && (
        <div className="rounded-r-md border-l-[3px] border-warning bg-warning-bg/50 px-3 py-1.5 text-[13px]">
          <strong>Even Better If:</strong> <Mdi>{f.even_better_if}</Mdi>
        </div>
      )}
      <div className="text-[13px]">
        <strong>My improvement (green pen):</strong>{" "}
        <Mdi>{f.improvement_prompt}</Mdi>
      </div>
    </Part>
  );
}

function revisionUrl(raw: string): string | undefined {
  try {
    const url = new URL(raw.includes(":") ? raw : `https://${raw}`);
    const sites = [
      "bbc.co.uk",
      "corbettmaths.com",
      "drfrostmaths.com",
      "physicsandmathstutor.com",
      "senecalearning.com",
    ];
    return url.protocol === "https:" &&
      !url.username &&
      !url.password &&
      !url.port &&
      sites.some(
        (host) => url.hostname === host || url.hostname.endsWith(`.${host}`),
      )
      ? url.href
      : undefined;
  } catch {
    return undefined;
  }
}

function RevisionView({ list, n }: { list: RevisionResource[]; n: number }) {
  return (
    <Part number={n} title="Revision Material: study at home">
      {list.map((r, i) => (
        <div key={i} className="text-[13px]">
          <div className="font-bold text-primary">{r.name}</div>
          <a
            href={revisionUrl(r.url)}
            target="_blank"
            rel="noopener noreferrer"
            className="break-all text-xs text-primary underline"
          >
            {r.url}
          </a>
          <div className="text-xs text-muted-foreground">
            <Mdi>{r.purpose}</Mdi>
          </div>
        </div>
      ))}
    </Part>
  );
}

/** One Fulwood lesson in student-resource form. `index` addresses it in the draft. */
export function LessonView({
  lesson,
  index,
  hasBoard,
}: {
  lesson: Lesson;
  index: number;
  hasBoard: boolean;
}) {
  const base: P = ["lessons", index];
  let n = 0;
  const next = () => ++n;
  return (
    <article
      id={`lesson-${index + 1}`}
      className="flex scroll-mt-32 flex-col gap-3"
    >
      <header className="border-b-2 border-primary pb-2">
        <h3 className="m-0 text-lg font-bold text-foreground">
          <MdField
            path={[...base, "title"]}
            value={lesson.title}
            label={`Lesson ${index + 1} title`}
            inline
          />
        </h3>
        <p className="m-0 mt-0.5 text-[13px] text-muted-foreground">
          <MdField
            path={[...base, "lesson_focus"]}
            value={lesson.lesson_focus}
            label={`Lesson ${index + 1} focus`}
            inline
          />
        </p>
        {lesson.objectives?.length > 0 && (
          <ul className="m-0 mt-2 list-disc pl-5 text-[13px] text-text-secondary">
            {lesson.objectives.map((o, i) => (
              <li key={i}>
                <Mdi>{o}</Mdi>
              </li>
            ))}
          </ul>
        )}
      </header>
      {lesson.din && <DinView d={lesson.din} base={base} n={next()} />}
      {lesson.key_spellings?.length ? (
        <SpellingsView list={lesson.key_spellings} n={next()} />
      ) : null}
      {lesson.keyword_bank?.length ? (
        <KeywordsView
          list={lesson.keyword_bank}
          hasBoard={hasBoard}
          n={next()}
        />
      ) : null}
      {lesson.i_do?.length ? (
        <IDoView steps={lesson.i_do} base={base} n={next()} />
      ) : null}
      {lesson.misconceptions?.length ? (
        <MisconceptionsView list={lesson.misconceptions} n={next()} />
      ) : null}
      {lesson.we_do?.length ? (
        <WeDoView tasks={lesson.we_do} base={base} n={next()} />
      ) : null}
      {lesson.you_do && <YouDoView y={lesson.you_do} base={base} n={next()} />}
      {lesson.exam_language_coach?.length ? (
        <ExamCoachView list={lesson.exam_language_coach} n={next()} />
      ) : null}
      {lesson.exam_questions?.length ? (
        <ExamQuestionsView
          list={lesson.exam_questions}
          base={base}
          n={next()}
        />
      ) : null}
      {lesson.exit_ticket?.length ? (
        <ExitTicketView
          list={lesson.exit_ticket}
          answers={lesson.exit_ticket_answers}
          base={base}
          n={next()}
        />
      ) : null}
      {lesson.feedback && <FeedbackView f={lesson.feedback} n={next()} />}
      {lesson.revision_resources?.length ? (
        <RevisionView list={lesson.revision_resources} n={next()} />
      ) : null}
    </article>
  );
}
