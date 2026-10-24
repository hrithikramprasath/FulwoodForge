import { RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { InlineProblem } from "@/components/preview/ErrorState";

/** Layout-matched table placeholder (loading state). */
export function TableSkeleton({
  rows = 5,
  cols = 5,
}: {
  rows?: number;
  cols?: number;
}) {
  return (
    <div
      className="flex flex-col gap-2 p-3"
      aria-busy="true"
      aria-label="Loading"
    >
      {Array.from({ length: rows }, (_, r) => (
        <div key={r} className="flex gap-3">
          {Array.from({ length: cols }, (_, c) => (
            <Skeleton key={c} className="h-5 flex-1" />
          ))}
        </div>
      ))}
    </div>
  );
}

/** Error state with retry. */
export function PanelError({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry: () => void;
}) {
  return (
    <div className="flex flex-col items-start gap-2 p-3">
      <InlineProblem error={error} />
      <Button variant="outline" size="sm" onClick={onRetry}>
        <RefreshCw className="size-4" strokeWidth={1.5} />
        Retry
      </Button>
    </div>
  );
}

/** Empty state row. */
export function PanelEmpty({ children }: { children: React.ReactNode }) {
  return (
    <p className="m-0 p-6 text-center text-[13px] text-muted-foreground">
      {children}
    </p>
  );
}

/** Bordered surface that hosts a dense table. */
export function Surface({
  title,
  actions,
  children,
}: {
  title?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-card">
      {(title || actions) && (
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2">
          {title && <h3 className="m-0 text-[13px] font-bold">{title}</h3>}
          {actions}
        </div>
      )}
      {children}
    </div>
  );
}
