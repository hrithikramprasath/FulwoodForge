import { CircleAlert, RefreshCw, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardKicker, CardTitle } from "@/components/ui/card";
import { ApiProblemError } from "@/lib/api";

/** Human summary of any thrown value, preferring the RFC 9457 problem document. */
export function describeProblem(error: unknown): {
  title: string;
  detail: string;
  requestId: string | null;
  retryAfterSeconds: number | null;
} {
  if (error instanceof ApiProblemError) {
    return {
      title: error.title,
      detail: error.detail,
      requestId: error.requestId,
      retryAfterSeconds: error.retryAfterSeconds,
    };
  }
  return {
    title: "Something went wrong",
    detail:
      error instanceof Error ? error.message : "An unexpected error occurred.",
    requestId: null,
    retryAfterSeconds: null,
  };
}

/** Non-destructive banner: reports a failure without replacing the document on screen. */
export function InlineProblem({
  error,
  onDismiss,
}: {
  error: unknown;
  onDismiss?: () => void;
}) {
  const p = describeProblem(error);
  return (
    <div
      role="alert"
      className="reveal no-print flex items-start gap-3 rounded-md border border-danger/30 bg-danger-bg px-3 py-2.5 text-[13px] text-danger"
    >
      <CircleAlert
        className="mt-0.5 size-4 shrink-0"
        strokeWidth={1.5}
        aria-hidden="true"
      />
      <div className="min-w-0 flex-1">
        <p className="m-0 font-semibold">{p.title}</p>
        <p className="m-0 text-xs">{p.detail}</p>
        {p.requestId && (
          <p className="tabular-nums m-0 mt-0.5 text-[11px] opacity-80">
            Reference: {p.requestId}
          </p>
        )}
      </div>
      {onDismiss && (
        <button
          type="button"
          aria-label="Dismiss message"
          onClick={onDismiss}
          className="rounded p-0.5 hover:bg-danger/10"
        >
          <X className="size-4" strokeWidth={1.5} />
        </button>
      )}
    </div>
  );
}

/** Full-canvas state for a failed generation (State 5 of the matrix: error). */
export function ErrorState({
  error,
  onRetry,
  onDismiss,
}: {
  error: unknown;
  onRetry: () => void;
  onDismiss: () => void;
}) {
  const p = describeProblem(error);
  return (
    <Card role="alert" className="reveal flex flex-col items-start gap-3 p-6">
      <CircleAlert
        className="size-6 text-danger"
        strokeWidth={1.5}
        aria-hidden="true"
      />
      <CardKicker className="text-danger">Generation failed</CardKicker>
      <CardTitle>{p.title}</CardTitle>
      <p className="m-0 text-[13px] text-text-secondary">{p.detail}</p>
      {p.retryAfterSeconds !== null && (
        <p className="m-0 text-xs text-muted-foreground">
          Try again in{" "}
          <span className="tabular-nums">{p.retryAfterSeconds}</span> seconds.
        </p>
      )}
      {p.requestId && (
        <p className="tabular-nums m-0 text-[11px] text-muted-foreground">
          Reference: {p.requestId}
        </p>
      )}
      <div className="flex gap-2 pt-1">
        <Button onClick={onRetry}>
          <RefreshCw className="size-4" strokeWidth={1.5} />
          Try again
        </Button>
        <Button variant="outline" onClick={onDismiss}>
          Back to the studio
        </Button>
      </div>
    </Card>
  );
}
