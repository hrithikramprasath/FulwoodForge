import { useEffect, useState } from "react";
import { Check, LoaderCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { TASK_LABELS } from "@/lib/adapt";
import type { TeacherRequest } from "@/types/content";

const STAGES = [
  "Validating curriculum specification",
  "Structuring Fulwood lesson spine",
  "Rendering KaTeX & OMML math notation",
  "Compiling mark schemes & differentiation tiers",
] as const;

/** Rough pacing of the four stages; the call itself reports no real progress. */
const STAGE_AT_SECONDS = [0, 4, 12, 24] as const;

function formatElapsed(ms: number): string {
  const total = ms / 1000;
  const minutes = Math.floor(total / 60);
  const seconds = total - minutes * 60;
  return `${String(minutes).padStart(2, "0")}:${seconds.toFixed(1).padStart(4, "0")}s`;
}

/**
 * Phased generation progress (State 1) over a layout-matched document skeleton
 * (State 5), with a Cancel control wired to the caller's AbortController.
 */
export function LoadingState({
  request,
  onCancel,
}: {
  request: TeacherRequest;
  onCancel: () => void;
}) {
  const [elapsedMs, setElapsedMs] = useState(0);

  useEffect(() => {
    const started = performance.now();
    const timer = window.setInterval(
      () => setElapsedMs(performance.now() - started),
      100,
    );
    return () => window.clearInterval(timer);
  }, []);

  const seconds = elapsedMs / 1000;
  let stage = 0;
  STAGE_AT_SECONDS.forEach((at, i) => {
    if (seconds >= at) stage = i;
  });

  return (
    <div className="reveal flex flex-col gap-4">
      <Card
        className="flex flex-col gap-4 p-5"
        role="status"
        aria-live="polite"
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="m-0 text-sm font-bold">
              Generating Year {request.year_group} {request.subject}{" "}
              {TASK_LABELS[request.task_type].toLowerCase()}:{" "}
              {request.topic.trim()}
            </p>
            <p className="m-0 mt-0.5 text-xs text-muted-foreground">
              {request.task_type === "booklet"
                ? "A booklet is several full lessons and usually takes one to three minutes."
                : "This usually takes well under a minute. A repeat of an earlier request appears straight away."}
            </p>
          </div>
          <span
            className="tabular-nums rounded bg-muted px-2 py-1 text-xs font-semibold"
            aria-label="Elapsed time"
          >
            {formatElapsed(elapsedMs)}
          </span>
        </div>
        <ol className="m-0 flex list-none flex-col gap-2 p-0">
          {STAGES.map((label, i) => {
            const done = i < stage;
            const active = i === stage;
            return (
              <li
                key={label}
                aria-current={active ? "step" : undefined}
                className={`flex items-center gap-2.5 text-[13px] ${
                  active
                    ? "font-semibold text-foreground"
                    : done
                      ? "text-muted-foreground"
                      : "text-border-strong"
                }`}
              >
                <span className="flex size-5 items-center justify-center">
                  {done ? (
                    <Check
                      className="size-4 text-success"
                      strokeWidth={2}
                      aria-hidden="true"
                    />
                  ) : active ? (
                    <LoaderCircle
                      className="size-4 animate-spin text-primary motion-reduce:animate-none"
                      strokeWidth={1.5}
                      aria-hidden="true"
                    />
                  ) : (
                    <span
                      className="size-1.5 rounded-full bg-current"
                      aria-hidden="true"
                    />
                  )}
                </span>
                <span className="tabular-nums text-xs text-muted-foreground">
                  {i + 1}.
                </span>
                {label}
              </li>
            );
          })}
        </ol>
        <div>
          <Button variant="outline" size="sm" onClick={onCancel}>
            Cancel Generation
          </Button>
        </div>
      </Card>

      {/* Layout-matched document sheet */}
      <Card className="flex flex-col gap-4 p-8" aria-hidden="true">
        <Skeleton className="h-3 w-40" />
        <Skeleton className="h-8 w-3/4" />
        <div className="grid grid-cols-4 gap-4">
          <Skeleton className="h-8" />
          <Skeleton className="h-8" />
          <Skeleton className="h-8" />
          <Skeleton className="h-8" />
        </div>
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            className="flex flex-col gap-2 rounded-md border border-border p-4"
          >
            <Skeleton className="h-5 w-1/3" />
            <Skeleton className="h-3 w-full" />
            <Skeleton className="h-3 w-11/12" />
            <Skeleton className="h-3 w-2/3" />
          </div>
        ))}
      </Card>
    </div>
  );
}
