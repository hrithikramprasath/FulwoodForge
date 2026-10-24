import { Skeleton } from "@/components/ui/skeleton";
import { PanelError } from "@/components/admin/shared";
import { api } from "@/lib/api";
import { formatCount, formatPercent, formatUsd } from "@/lib/format";
import { useResource } from "@/lib/useResource";

function Stat({
  label,
  value,
  sub,
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-1 bg-card p-3">
      <dt className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
      </dt>
      <dd className="tabular-nums m-0 truncate text-xl font-semibold text-foreground">
        {value}
      </dd>
      {sub && (
        <dd className="tabular-nums m-0 truncate text-[11px] text-muted-foreground">
          {sub}
        </dd>
      )}
    </div>
  );
}

/** Operational telemetry ribbon from GET /api/admin/overview. */
export function OverviewRibbon() {
  const { data, error, loading, reload } = useResource("overview", (signal) =>
    api.admin.overview({ signal }),
  );

  if (error) return <PanelError error={error} onRetry={reload} />;
  if (loading || !data) {
    return (
      <div
        className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-border bg-border md:grid-cols-3 xl:grid-cols-6"
        aria-busy="true"
        aria-label="Loading telemetry"
      >
        {Array.from({ length: 6 }, (_, i) => (
          <div key={i} className="flex flex-col gap-2 bg-card p-3">
            <Skeleton className="h-3 w-20" />
            <Skeleton className="h-6 w-16" />
          </div>
        ))}
      </div>
    );
  }

  return (
    <dl
      className="m-0 grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-border bg-border md:grid-cols-3 xl:grid-cols-6"
      aria-label="Platform telemetry"
    >
      <Stat
        label="Total Educators"
        value={formatCount(data.total_users)}
        sub={`${formatCount(data.active_users)} active / ${formatCount(data.pending_users)} pending`}
      />
      <Stat label="Active Sessions" value={formatCount(data.active_sessions)} />
      <Stat label="24h Generations" value={formatCount(data.generations_24h)} />
      <Stat
        label="24h Cache Hit Rate"
        value={formatPercent(data.cache_hit_rate_24h)}
      />
      <Stat
        label="24h Token Volume"
        value={formatCount(data.total_tokens_24h)}
      />
      <Stat
        label="Estimated API Spend"
        value={formatUsd(data.total_cost_usd_24h)}
        sub={`24h / cumulative ${formatUsd(data.cumulative_cost_usd)}`}
      />
    </dl>
  );
}
