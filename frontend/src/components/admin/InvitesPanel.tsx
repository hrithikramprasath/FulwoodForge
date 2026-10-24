import { useId, useState } from "react";
import { Plus } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { InlineProblem } from "@/components/preview/ErrorState";
import {
  PanelEmpty,
  PanelError,
  Surface,
  TableSkeleton,
} from "@/components/admin/shared";
import { api, ApiProblemError } from "@/lib/api";
import { formatUtc } from "@/lib/format";
import { usePagedResource } from "@/lib/useResource";
import type { InviteCodeRecord } from "@/types/content";

/** Invite code manager (GET/POST/DELETE /api/admin/invite-codes). */
export function InvitesPanel() {
  const uid = useId();
  const [code, setCode] = useState("");
  const [maxUses, setMaxUses] = useState("10");
  const [expiresHours, setExpiresHours] = useState("168");
  const [creating, setCreating] = useState(false);
  const [problem, setProblem] = useState<unknown>(null);
  const [busyUuid, setBusyUuid] = useState<string | null>(null);

  const list = usePagedResource<InviteCodeRecord>("invites", (cursor, signal) =>
    api.admin.inviteCodes({ cursor, limit: 25 }, { signal }),
  );
  const fieldErrors =
    problem instanceof ApiProblemError ? problem.invalidParams : {};

  async function create(event: React.FormEvent) {
    event.preventDefault();
    setCreating(true);
    setProblem(null);
    try {
      await api.admin.createInviteCode({
        code: code.trim() || null,
        max_uses: Number(maxUses),
        expires_in_hours:
          expiresHours.trim() === "" ? null : Number(expiresHours),
      });
      setCode("");
      list.reload();
    } catch (err) {
      setProblem(err);
    } finally {
      setCreating(false);
    }
  }

  async function deactivate(record: InviteCodeRecord) {
    setBusyUuid(record.uuid);
    setProblem(null);
    try {
      await api.admin.deactivateInviteCode(record.uuid);
      list.update((items) =>
        items.map((i) =>
          i.uuid === record.uuid ? { ...i, is_active: false } : i,
        ),
      );
    } catch (err) {
      setProblem(err);
    } finally {
      setBusyUuid(null);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <Surface title="Create invite code">
        <form
          onSubmit={create}
          className="flex flex-wrap items-start gap-3 p-3"
          noValidate
        >
          <Field
            label="Code (blank to auto-generate)"
            htmlFor={`${uid}-code`}
            error={fieldErrors["code"]}
            className="min-w-48 flex-1"
          >
            <Input
              id={`${uid}-code`}
              value={code}
              placeholder="FULWOOD-2026"
              onChange={(e) => setCode(e.target.value)}
              aria-invalid={Boolean(fieldErrors["code"])}
            />
          </Field>
          <Field
            label="Max uses"
            htmlFor={`${uid}-max`}
            error={fieldErrors["max_uses"]}
            className="w-28"
          >
            <Input
              id={`${uid}-max`}
              type="number"
              min={1}
              max={1000}
              inputMode="numeric"
              className="tabular-nums"
              value={maxUses}
              onChange={(e) => setMaxUses(e.target.value)}
              aria-invalid={Boolean(fieldErrors["max_uses"])}
            />
          </Field>
          <Field
            label="Expires in (hours)"
            htmlFor={`${uid}-exp`}
            error={fieldErrors["expires_in_hours"]}
            className="w-36"
          >
            <Input
              id={`${uid}-exp`}
              type="number"
              min={1}
              inputMode="numeric"
              className="tabular-nums"
              value={expiresHours}
              onChange={(e) => setExpiresHours(e.target.value)}
              aria-invalid={Boolean(fieldErrors["expires_in_hours"])}
            />
          </Field>
          <Button type="submit" loading={creating} className="mt-[22px]">
            <Plus className="size-4" strokeWidth={1.5} />
            Create Code
          </Button>
        </form>
      </Surface>
      {problem !== null && Object.keys(fieldErrors).length === 0 && (
        <InlineProblem error={problem} onDismiss={() => setProblem(null)} />
      )}
      <Surface title="Invite codes">
        {list.loading ? (
          <TableSkeleton cols={5} />
        ) : list.error ? (
          <PanelError error={list.error} onRetry={list.reload} />
        ) : list.items.length === 0 ? (
          <PanelEmpty>
            No invite codes yet. Create one above to onboard educators.
          </PanelEmpty>
        ) : (
          <Table aria-label="Invite codes">
            <TableHeader>
              <TableRow>
                <TableHead>Code</TableHead>
                <TableHead>Usage</TableHead>
                <TableHead>Expires</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {list.items.map((record) => (
                <TableRow key={record.uuid}>
                  <TableCell className="tabular-nums font-semibold">
                    {record.code}
                  </TableCell>
                  <TableCell className="tabular-nums">
                    {record.uses_count} / {record.max_uses}
                  </TableCell>
                  <TableCell className="tabular-nums text-xs text-muted-foreground">
                    {record.expires_at ? formatUtc(record.expires_at) : "Never"}
                  </TableCell>
                  <TableCell>
                    <Badge variant={record.is_active ? "success" : "neutral"}>
                      {record.is_active ? "ACTIVE" : "INACTIVE"}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-right">
                    {record.is_active && (
                      <Button
                        size="sm"
                        variant="outline"
                        className="h-7 text-xs"
                        disabled={busyUuid === record.uuid}
                        onClick={() => deactivate(record)}
                        aria-label={`Deactivate ${record.code}`}
                      >
                        Deactivate
                      </Button>
                    )}
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
    </div>
  );
}
