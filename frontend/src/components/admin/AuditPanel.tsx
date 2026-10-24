import { Fragment, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  PanelEmpty,
  PanelError,
  Surface,
  TableSkeleton,
} from "@/components/admin/shared";
import { api } from "@/lib/api";
import { formatCount, formatUsd, formatUtc } from "@/lib/format";
import { usePagedResource } from "@/lib/useResource";
import type { AuditEventRecord, TokenLedgerRecord } from "@/types/content";

/** Security and activity audit log (GET /api/admin/audit-events). */
export function AuditLogPanel() {
  const [typeInput, setTypeInput] = useState("");
  const [eventType, setEventType] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);

  const list = usePagedResource<AuditEventRecord>(
    `audit:${eventType}`,
    (cursor, signal) =>
      api.admin.auditEvents(
        { cursor, limit: 50, event_type: eventType || undefined },
        { signal },
      ),
  );

  return (
    <div className="flex flex-col gap-3">
      <form
        className="flex items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          setEventType(typeInput.trim());
        }}
      >
        <Input
          aria-label="Filter by event type"
          placeholder="Filter by event_type, e.g. LOGIN_FAILED"
          className="max-w-xs"
          value={typeInput}
          onChange={(e) => setTypeInput(e.target.value)}
        />
        <Button type="submit" variant="outline" size="sm">
          Apply
        </Button>
      </form>
      <Surface>
        {list.loading ? (
          <TableSkeleton cols={5} />
        ) : list.error ? (
          <PanelError error={list.error} onRetry={list.reload} />
        ) : list.items.length === 0 ? (
          <PanelEmpty>No audit events match this filter.</PanelEmpty>
        ) : (
          <Table aria-label="Audit events">
            <TableHeader>
              <TableRow>
                <TableHead>Timestamp</TableHead>
                <TableHead>Event</TableHead>
                <TableHead>Actor</TableHead>
                <TableHead>Target</TableHead>
                <TableHead>IP address</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {list.items.map((e) => {
                const open = expanded === e.uuid;
                return (
                  <Fragment key={e.uuid}>
                    <TableRow>
                      <TableCell className="tabular-nums text-xs text-muted-foreground">
                        {formatUtc(e.created_at)}
                      </TableCell>
                      <TableCell>
                        <button
                          type="button"
                          aria-expanded={open}
                          onClick={() => setExpanded(open ? null : e.uuid)}
                          className="fx-transition inline-flex items-center gap-1 rounded text-left text-[13px] font-semibold hover:text-primary"
                        >
                          {open ? (
                            <ChevronDown
                              className="size-4"
                              strokeWidth={1.5}
                              aria-hidden="true"
                            />
                          ) : (
                            <ChevronRight
                              className="size-4"
                              strokeWidth={1.5}
                              aria-hidden="true"
                            />
                          )}
                          {e.event_type}
                        </button>
                      </TableCell>
                      <TableCell>
                        {e.actor_username ? `@${e.actor_username}` : "System"}
                      </TableCell>
                      <TableCell>
                        {e.target_username ? `@${e.target_username}` : "None"}
                      </TableCell>
                      <TableCell className="tabular-nums text-xs">
                        {e.ip_address}
                      </TableCell>
                    </TableRow>
                    {open && (
                      <TableRow>
                        <TableCell colSpan={5} className="bg-muted">
                          <pre
                            data-testid="audit-metadata"
                            className="m-0 overflow-x-auto font-mono text-xs"
                          >
                            {JSON.stringify(e.metadata, null, 2)}
                          </pre>
                        </TableCell>
                      </TableRow>
                    )}
                  </Fragment>
                );
              })}
            </TableBody>
          </Table>
        )}
        {list.hasMore && !list.loading && (
          <div className="border-t border-border p-2">
            <Button
              variant="outline"
              size="sm"
              loading={list.moreBusy}
              onClick={list.loadMore}
            >
              Load More
            </Button>
          </div>
        )}
      </Surface>
    </div>
  );
}

const LEDGER_VARIANT = {
  SUCCESS: "success",
  CACHE_HIT: "default",
  UPSTREAM_ERROR: "danger",
} as const;

/** Gemini token and cost ledger (GET /api/admin/token-ledger). */
export function TokenLedgerPanel() {
  const list = usePagedResource<TokenLedgerRecord>("ledger", (cursor, signal) =>
    api.admin.tokenLedger({ cursor, limit: 50 }, { signal }),
  );
  return (
    <Surface>
      {list.loading ? (
        <TableSkeleton cols={8} />
      ) : list.error ? (
        <PanelError error={list.error} onRetry={list.reload} />
      ) : list.items.length === 0 ? (
        <PanelEmpty>No Gemini usage recorded yet.</PanelEmpty>
      ) : (
        <Table aria-label="Token ledger">
          <TableHeader>
            <TableRow>
              <TableHead>Timestamp</TableHead>
              <TableHead>User</TableHead>
              <TableHead>Model</TableHead>
              <TableHead className="text-right">Prompt</TableHead>
              <TableHead className="text-right">Candidate</TableHead>
              <TableHead className="text-right">Total</TableHead>
              <TableHead className="text-right">Cost</TableHead>
              <TableHead className="text-right">Latency</TableHead>
              <TableHead>Status</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {list.items.map((r) => (
              <TableRow key={r.uuid}>
                <TableCell className="tabular-nums text-xs text-muted-foreground">
                  {formatUtc(r.created_at)}
                </TableCell>
                <TableCell>@{r.username}</TableCell>
                <TableCell className="text-xs">{r.model_name}</TableCell>
                <TableCell className="tabular-nums text-right">
                  {formatCount(r.prompt_tokens)}
                </TableCell>
                <TableCell className="tabular-nums text-right">
                  {formatCount(r.candidate_tokens)}
                </TableCell>
                <TableCell className="tabular-nums text-right">
                  {formatCount(r.total_tokens)}
                </TableCell>
                <TableCell className="tabular-nums text-right">
                  {formatUsd(r.estimated_cost_usd)}
                </TableCell>
                <TableCell className="tabular-nums text-right">
                  {formatCount(r.latency_ms)} ms
                </TableCell>
                <TableCell>
                  <Badge variant={LEDGER_VARIANT[r.status]}>{r.status}</Badge>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {list.hasMore && !list.loading && (
        <div className="border-t border-border p-2">
          <Button
            variant="outline"
            size="sm"
            loading={list.moreBusy}
            onClick={list.loadMore}
          >
            Load More
          </Button>
        </div>
      )}
    </Surface>
  );
}
