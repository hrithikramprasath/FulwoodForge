import { useEffect, useState } from "react";
import {
  BookOpen,
  Archive,
  CircleAlert,
  Library,
  LoaderCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { InlineProblem } from "@/components/preview/ErrorState";
import { TASK_LABELS } from "@/lib/adapt";
import { api, isAbortError } from "@/lib/api";
import { formatUtc } from "@/lib/format";
import type { SavedLessonDetail } from "@/types/api";
import type {
  SavedLessonSummary,
  SubjectLiteral,
  TaskType,
} from "@/types/content";

const ALL = "ALL";
const PAGE_SIZE = 12;

interface PageState {
  key: string;
  items: SavedLessonSummary[];
  next: string | null;
  error: unknown;
}

export function LessonLibraryDrawer({
  allowedSubjects,
  allowedTaskTypes,
  activeUuid,
  refreshKey,
  onOpenLesson,
  onDeleted,
}: {
  allowedSubjects: readonly SubjectLiteral[];
  allowedTaskTypes: readonly TaskType[];
  activeUuid: string | null;
  /** Bump to reload the first page (after a new generation is saved). */
  refreshKey: number;
  onOpenLesson: (lesson: SavedLessonDetail) => void;
  onDeleted: (uuid: string) => void;
}) {
  const [subject, setSubject] = useState(ALL);
  const [taskType, setTaskType] = useState(ALL);
  const [page, setPage] = useState<PageState | null>(null);
  const [hidden, setHidden] = useState<ReadonlySet<string>>(new Set());
  const [moreBusy, setMoreBusy] = useState(false);
  const [openingUuid, setOpeningUuid] = useState<string | null>(null);
  const [problem, setProblem] = useState<unknown>(null);

  const queryKey = `${subject}|${taskType}|${refreshKey}`;
  const loading = page?.key !== queryKey;

  useEffect(() => {
    const controller = new AbortController();
    api.lessons
      .list(
        {
          limit: PAGE_SIZE,
          subject: subject === ALL ? undefined : subject,
          task_type: taskType === ALL ? "" : (taskType as TaskType),
        },
        { signal: controller.signal },
      )
      .then((r) =>
        setPage({
          key: queryKey,
          items: r.items,
          next: r.next_cursor,
          error: null,
        }),
      )
      .catch((error: unknown) => {
        if (!isAbortError(error))
          setPage({ key: queryKey, items: [], next: null, error });
      });
    return () => controller.abort();
  }, [queryKey, subject, taskType]);

  const items = (loading ? [] : (page?.items ?? [])).filter(
    (l) => !hidden.has(l.uuid),
  );
  const filtered = subject !== ALL || taskType !== ALL;

  async function loadMore() {
    if (!page?.next) return;
    setMoreBusy(true);
    try {
      const r = await api.lessons.list({
        cursor: page.next,
        limit: PAGE_SIZE,
        subject: subject === ALL ? undefined : subject,
        task_type: taskType === ALL ? "" : (taskType as TaskType),
      });
      setPage({
        ...page,
        items: [...page.items, ...r.items],
        next: r.next_cursor,
      });
    } catch (err) {
      setProblem(err);
    } finally {
      setMoreBusy(false);
    }
  }

  async function open(summary: SavedLessonSummary) {
    setOpeningUuid(summary.uuid);
    setProblem(null);
    try {
      onOpenLesson(await api.lessons.get(summary.uuid));
    } catch (err) {
      setProblem(err);
    } finally {
      setOpeningUuid(null);
    }
  }

  async function archive(summary: SavedLessonSummary) {
    // Optimistic: hide the card now, bring it back if the server refuses.
    setHidden((h) => new Set(h).add(summary.uuid));
    setProblem(null);
    try {
      await api.lessons.remove(summary.uuid);
      onDeleted(summary.uuid);
    } catch (err) {
      setHidden((h) => {
        const next = new Set(h);
        next.delete(summary.uuid);
        return next;
      });
      setProblem(err);
    }
  }

  return (
    <section className="flex flex-col gap-3" aria-label="Saved lesson library">
      <div className="grid grid-cols-2 gap-2">
        <Select value={subject} onValueChange={setSubject}>
          <SelectTrigger aria-label="Filter by subject">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All subjects</SelectItem>
            {allowedSubjects.map((s) => (
              <SelectItem key={s} value={s}>
                {s}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={taskType} onValueChange={setTaskType}>
          <SelectTrigger aria-label="Filter by task type">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All types</SelectItem>
            {allowedTaskTypes.map((t) => (
              <SelectItem key={t} value={t}>
                {TASK_LABELS[t]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {problem !== null && (
        <InlineProblem error={problem} onDismiss={() => setProblem(null)} />
      )}

      {loading && (
        <ul
          className="m-0 flex list-none flex-col gap-2 p-0"
          aria-busy="true"
          aria-label="Loading lessons"
        >
          {[0, 1, 2, 3].map((i) => (
            <li
              key={i}
              className="flex flex-col gap-2 rounded-md border border-border bg-card p-3"
            >
              <Skeleton className="h-4 w-4/5" />
              <div className="flex gap-1.5">
                <Skeleton className="h-4 w-16" />
                <Skeleton className="h-4 w-12" />
              </div>
              <Skeleton className="h-3 w-1/2" />
            </li>
          ))}
        </ul>
      )}

      {!loading && page?.error !== null && page?.error !== undefined && (
        <InlineProblem error={page.error} />
      )}

      {!loading && !page?.error && items.length === 0 && (
        <div className="flex flex-col items-center gap-2 rounded-md border border-dashed border-border-strong px-4 py-8 text-center">
          <Library
            className="size-8 text-muted-foreground"
            strokeWidth={1.5}
            aria-hidden="true"
          />
          <p className="m-0 text-[13px] font-semibold">
            {filtered
              ? "No lessons match these filters"
              : "No saved lessons yet"}
          </p>
          <p className="m-0 text-xs text-muted-foreground">
            {filtered
              ? "Clear a filter to see the rest of your library."
              : "Resources you generate are saved here automatically."}
          </p>
        </div>
      )}

      {!loading && items.length > 0 && (
        <ul className="m-0 flex list-none flex-col gap-2 p-0">
          {items.map((l) => {
            const active = l.uuid === activeUuid;
            return (
              <li
                key={l.uuid}
                className={`fx-transition flex flex-col gap-2 rounded-md border bg-card p-3 hover:border-border-strong ${
                  active ? "border-primary" : "border-border"
                }`}
              >
                <button
                  type="button"
                  onClick={() => open(l)}
                  disabled={openingUuid !== null}
                  className="flex flex-col gap-1.5 rounded text-left"
                  aria-label={`Open ${l.title}`}
                >
                  <span className="flex items-start gap-2">
                    <span className="min-w-0 flex-1 text-[13px] font-bold leading-snug">
                      {l.title}
                    </span>
                    {openingUuid === l.uuid && (
                      <LoaderCircle
                        className="size-4 shrink-0 animate-spin text-primary motion-reduce:animate-none"
                        strokeWidth={1.5}
                        aria-hidden="true"
                      />
                    )}
                  </span>
                  <span className="flex flex-wrap items-center gap-1.5">
                    <Badge>{l.subject}</Badge>
                    <Badge variant="neutral">
                      Year {l.year_group}
                      {l.tier ? ` \u00b7 ${l.tier}` : ""}
                    </Badge>
                    <Badge variant="neutral">{TASK_LABELS[l.task_type]}</Badge>
                    {l.slide_count > 0 && (
                      <Badge variant="neutral" className="tabular-nums">
                        {l.slide_count}{" "}
                        {l.task_type === "powerpoint" ? "slides" : "lessons"}
                      </Badge>
                    )}
                  </span>
                  <span className="tabular-nums text-[11px] text-muted-foreground">
                    {formatUtc(l.created_at)}
                  </span>
                </button>
                <div className="flex gap-1.5 border-t border-border pt-2">
                  <Button
                    size="sm"
                    variant={active ? "secondary" : "outline"}
                    className="h-7 text-xs"
                    onClick={() => open(l)}
                    disabled={openingUuid !== null}
                  >
                    <BookOpen className="size-3.5" strokeWidth={1.5} />
                    {active ? "Open" : "Load"}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="ml-auto h-7 text-xs text-danger hover:bg-danger-bg hover:text-danger"
                    onClick={() => archive(l)}
                    aria-label={`Archive ${l.title}`}
                  >
                    <Archive className="size-3.5" strokeWidth={1.5} />
                    Archive
                  </Button>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {!loading && page?.next && (
        <Button variant="outline" block loading={moreBusy} onClick={loadMore}>
          Load More
        </Button>
      )}
      {!loading && page?.error ? (
        <p className="m-0 flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <CircleAlert
            className="size-3.5"
            strokeWidth={1.5}
            aria-hidden="true"
          />
          Library unavailable.
        </p>
      ) : null}
    </section>
  );
}
