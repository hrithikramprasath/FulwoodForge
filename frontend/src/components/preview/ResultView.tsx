import { useEffect, useRef, useState } from "react";
import {
  FileDown,
  ListTree,
  Pencil,
  Save,
  ShieldCheck,
  ChevronsDownUp,
  ChevronsUpDown,
  X,
} from "lucide-react";
import "katex/dist/katex.min.css";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { InlineProblem } from "@/components/preview/ErrorState";
import { AnswerKeyView } from "@/components/preview/AnswerKeyView";
import { countAnswers, hasAnswers } from "@/components/preview/answers";
import { EditContext, SectionsContext } from "@/components/preview/fields";
import { LessonView } from "@/components/preview/LessonSections";
import { Mdi } from "@/components/preview/Markdown";
import { ANSWER_FORMATS, FORMAT_AVAILABILITY, TASK_LABELS } from "@/lib/adapt";
import { api, isAbortError } from "@/lib/api";
import { setIn, type EditPath } from "@/lib/editPath";
import {
  exportResource,
  exportSavedLesson,
  type ExportFormat,
} from "@/lib/exportFile";
import type { GeneratedContent } from "@/types/content";

const FORMATS = ["pdf", "docx", "pptx"] as const;

const SOURCE_LABELS: Record<string, string> = {
  cache: "Reused your earlier result",
  fallback: "Example: AI unavailable",
  offline: "Example: not AI generated",
};

type Tab = "student" | "answers";

export interface ResultViewProps {
  content: GeneratedContent;
  /** Saved-lesson id; enables PATCH persistence and cached exports. */
  lessonUuid: string | null;
  /** Called with the new content once an inline edit is committed. */
  onContentChange: (content: GeneratedContent) => void;
}

export function ResultView({
  content,
  lessonUuid,
  onContentChange,
}: ResultViewProps) {
  const [activeTab, setActiveTab] = useState<Tab>("student");
  const [draft, setDraft] = useState<GeneratedContent | null>(null);
  const [sections, setSections] = useState({ allOpen: true, epoch: 0 });
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved">(
    "idle",
  );
  const [problem, setProblem] = useState<unknown>(null);
  const [busyFormat, setBusyFormat] = useState<ExportFormat | null>(null);
  const exportAbort = useRef<AbortController | null>(null);

  const editing = draft !== null;
  const shown = draft ?? content;
  const availability = FORMAT_AVAILABILITY[shown.task_type];
  const answered = hasAnswers(shown);
  const answerCount = countAnswers(shown);
  // The Answer Key tab is enabled whenever ANY answer exists (VULN-08).
  const tab: Tab = answered ? activeTab : "student";

  useEffect(() => {
    return () => exportAbort.current?.abort();
  }, []);

  const edit = {
    editing,
    set: (path: EditPath, value: string) =>
      setDraft((d) => (d ? setIn(d, path, value) : d)),
  };

  async function save() {
    if (!draft) return;
    const previous = content;
    const next = draft;
    setProblem(null);
    setSaveState("saving");
    // Optimistic: show the edit immediately, roll back if the server refuses it.
    onContentChange(next);
    setDraft(null);
    if (!lessonUuid) {
      setSaveState("saved");
      return;
    }
    try {
      const saved = await api.lessons.update(lessonUuid, {
        title: next.title,
        content: next,
      });
      onContentChange(saved.content);
      setSaveState("saved");
    } catch (err) {
      onContentChange(previous);
      setDraft(next);
      setProblem(err);
      setSaveState("idle");
    }
  }

  async function handleExport(format: ExportFormat) {
    setProblem(null);
    exportAbort.current?.abort();
    const controller = new AbortController();
    exportAbort.current = controller;
    setBusyFormat(format);
    try {
      if (lessonUuid && !editing) {
        await exportSavedLesson(
          lessonUuid,
          content.title,
          format,
          controller.signal,
        );
      } else {
        await exportResource(shown, format, controller.signal);
      }
    } catch (err) {
      // Non-destructive: the on-screen document is left exactly as it was.
      if (!isAbortError(err)) setProblem(err);
    } finally {
      if (exportAbort.current === controller) {
        exportAbort.current = null;
        setBusyFormat(null);
      }
    }
  }

  function jumpTo(index: number) {
    document
      .getElementById(`lesson-${index + 1}`)
      ?.scrollIntoView({ behavior: "smooth" });
  }

  return (
    <EditContext.Provider value={edit}>
      <SectionsContext.Provider value={sections}>
        <div className="flex min-w-0 flex-col gap-3">
          {/* Sticky studio toolbar */}
          <div
            className="no-print sticky top-0 z-20 flex flex-col gap-2 rounded-lg border border-border bg-card/95 p-2.5 backdrop-blur"
            role="toolbar"
            aria-label="Studio toolbar"
          >
            <div className="flex flex-wrap items-center gap-2">
              <SegmentedControl<Tab>
                ariaLabel="Document view"
                value={tab}
                onChange={setActiveTab}
                options={[
                  { value: "student", label: "Student Resource" },
                  {
                    value: "answers",
                    label: "Answer Key & Mark Scheme",
                    count: answerCount,
                    disabled: !answered,
                  },
                ]}
              />
              {shown.lessons.length > 1 && (
                <nav
                  aria-label="Lesson outline"
                  className="flex items-center gap-1"
                >
                  <ListTree
                    className="size-4 text-muted-foreground"
                    strokeWidth={1.5}
                    aria-hidden="true"
                  />
                  {shown.lessons.map((_, i) => (
                    <Button
                      key={i}
                      variant="ghost"
                      size="sm"
                      className="tabular-nums h-7 px-2"
                      onClick={() => jumpTo(i)}
                      aria-label={`Jump to lesson ${i + 1}`}
                    >
                      L{i + 1}
                    </Button>
                  ))}
                </nav>
              )}
              <div className="ml-auto flex items-center gap-1.5">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    setSections((s) => ({
                      allOpen: !s.allOpen,
                      epoch: s.epoch + 1,
                    }))
                  }
                >
                  {sections.allOpen ? (
                    <ChevronsDownUp className="size-4" strokeWidth={1.5} />
                  ) : (
                    <ChevronsUpDown className="size-4" strokeWidth={1.5} />
                  )}
                  {sections.allOpen ? "Collapse all" : "Expand all"}
                </Button>
                {editing ? (
                  <>
                    <Button
                      size="sm"
                      onClick={save}
                      loading={saveState === "saving"}
                    >
                      <Save className="size-4" strokeWidth={1.5} />
                      Save changes
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => {
                        setDraft(null);
                        setProblem(null);
                      }}
                    >
                      <X className="size-4" strokeWidth={1.5} />
                      Discard
                    </Button>
                  </>
                ) : (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      setSaveState("idle");
                      setDraft(
                        JSON.parse(JSON.stringify(content)) as GeneratedContent,
                      );
                    }}
                  >
                    <Pencil className="size-4" strokeWidth={1.5} />
                    Edit
                  </Button>
                )}
                {saveState === "saved" && !editing && (
                  <Badge variant="success" role="status">
                    Saved
                  </Badge>
                )}
              </div>
            </div>

            {/* Export action bar */}
            <div className="flex flex-wrap items-center gap-1.5 border-t border-border pt-2">
              <span className="mr-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                Export
              </span>
              {FORMATS.filter((f) => availability[f]).map((format) => (
                <Button
                  key={format}
                  size="sm"
                  variant={format === "pdf" ? "default" : "outline"}
                  loading={busyFormat === format}
                  disabled={busyFormat !== null && busyFormat !== format}
                  onClick={() => handleExport(format)}
                >
                  {busyFormat !== format && (
                    <FileDown className="size-4" strokeWidth={1.5} />
                  )}
                  .{format.toUpperCase()}
                </Button>
              ))}
              {answered &&
                ANSWER_FORMATS.map((format) => (
                  <Button
                    key={format}
                    size="sm"
                    variant="outline"
                    loading={busyFormat === format}
                    disabled={busyFormat !== null && busyFormat !== format}
                    onClick={() => handleExport(format)}
                  >
                    {busyFormat !== format && (
                      <ShieldCheck className="size-4" strokeWidth={1.5} />
                    )}
                    Answers .{format.split("-")[1].toUpperCase()}
                  </Button>
                ))}
              {busyFormat && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => exportAbort.current?.abort()}
                >
                  Cancel export
                </Button>
              )}
            </div>
          </div>

          {problem !== null && (
            <InlineProblem error={problem} onDismiss={() => setProblem(null)} />
          )}

          {/* Document sheet */}
          <article className="print-sheet reveal rounded-lg border border-border bg-card p-6 sm:p-8">
            <header className="mb-5 border-b-2 border-primary pb-4">
              <p className="m-0 text-[10px] font-semibold uppercase tracking-[0.14em] text-primary">
                Fulwood Curriculum &middot; {TASK_LABELS[shown.task_type]}
                {shown.spec_reference ? ` \u00b7 ${shown.spec_reference}` : ""}
              </p>
              <h2 className="m-0 mt-1 text-2xl font-bold tracking-tight text-foreground">
                {editing ? (
                  <Input
                    aria-label="Resource title"
                    value={shown.title}
                    onChange={(e) => edit.set(["title"], e.target.value)}
                    className="h-10 text-lg font-bold"
                  />
                ) : (
                  shown.title
                )}
              </h2>
              <dl className="m-0 mt-3 grid grid-cols-2 gap-x-6 gap-y-1 text-xs sm:grid-cols-4">
                {[
                  ["Subject", shown.subject],
                  ["Year group", shown.year_group],
                  ["Exam board", shown.exam_board ?? "General"],
                  ["Audience", shown.audience_note],
                ].map(([k, v]) => (
                  <div key={k} className="min-w-0">
                    <dt className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                      {k}
                    </dt>
                    <dd
                      className="m-0 truncate font-medium text-foreground"
                      title={v}
                    >
                      {v}
                    </dd>
                  </div>
                ))}
              </dl>
              {tab === "student" ? (
                <div className="mt-3 grid grid-cols-3 gap-4 text-xs text-muted-foreground">
                  {["Name", "Class", "Date"].map((label) => (
                    <div
                      key={label}
                      className="border-b border-border-strong pb-3"
                    >
                      {label}
                    </div>
                  ))}
                </div>
              ) : null}
              {shown.source && shown.source !== "gemini" && (
                <Badge
                  variant="warning"
                  className="mt-3 uppercase tracking-wider"
                >
                  {SOURCE_LABELS[shown.source]}
                </Badge>
              )}
            </header>

            {tab === "student" && shown.overview && (
              <section
                className="mb-5 rounded-md border border-border bg-surface-subtle p-4"
                aria-label="At a glance"
              >
                <p className="m-0 mb-1 text-[10px] font-bold uppercase tracking-[0.12em] text-primary">
                  At a glance
                </p>
                <p className="m-0 text-[13px] font-medium leading-relaxed">
                  <Mdi>{shown.overview.summary}</Mdi>
                </p>
                {shown.overview.objectives.length > 0 && (
                  <ul className="m-0 mt-2 list-disc pl-5 text-[13px]">
                    {shown.overview.objectives.map((o, i) => (
                      <li key={i}>
                        <Mdi>{o}</Mdi>
                      </li>
                    ))}
                  </ul>
                )}
                <p className="m-0 mt-2 text-[13px] text-text-secondary">
                  <Mdi>{shown.overview.assessment_focus}</Mdi>
                </p>
              </section>
            )}

            {tab === "answers" ? (
              <AnswerKeyView content={shown} />
            ) : (
              <div className="flex flex-col gap-8">
                {shown.lessons.map((lesson, i) => (
                  <LessonView
                    key={i}
                    lesson={lesson}
                    index={i}
                    hasBoard={Boolean(shown.exam_board)}
                  />
                ))}
                {shown.difficulty_note && (
                  <p className="m-0 border-t border-border pt-3 text-xs text-muted-foreground">
                    <strong>Pedagogical note:</strong> {shown.difficulty_note}
                  </p>
                )}
              </div>
            )}
          </article>

          <p className="no-print m-0 text-[11px] italic text-muted-foreground">
            AI can make mistakes. Always review generated content before using
            it in class.
          </p>
        </div>
      </SectionsContext.Provider>
    </EditContext.Provider>
  );
}
