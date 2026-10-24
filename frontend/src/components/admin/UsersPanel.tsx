import { useState } from "react";
import { Check, Copy, KeyRound, ShieldAlert } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
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
import { api } from "@/lib/api";
import { formatUtc } from "@/lib/format";
import { usePagedResource } from "@/lib/useResource";
import type {
  AdminPasswordResetResponse,
  AdminUserUpdateRequest,
  AuthenticatedUser,
  UserStatus,
} from "@/types/content";

type Filter = "ALL" | UserStatus;

const STATUS_VARIANT = {
  ACTIVE: "success",
  PENDING: "warning",
  SUSPENDED: "danger",
} as const;

/** Two-phase security dialog: confirm, then show the generated password exactly once. */
export function OneTimePasswordResetModal({
  user,
  onClose,
  onIssued,
}: {
  user: AuthenticatedUser | null;
  onClose: () => void;
  onIssued: () => void;
}) {
  const [result, setResult] = useState<AdminPasswordResetResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<unknown>(null);
  const [copied, setCopied] = useState(false);

  function close() {
    // The temporary password is never retained after the dialog closes.
    setResult(null);
    setProblem(null);
    setCopied(false);
    onClose();
  }

  async function issue() {
    if (!user) return;
    setBusy(true);
    setProblem(null);
    try {
      setResult(await api.admin.resetPassword(user.uuid));
      onIssued();
    } catch (err) {
      setProblem(err);
    } finally {
      setBusy(false);
    }
  }

  async function copy() {
    if (!result) return;
    try {
      await navigator.clipboard.writeText(result.temporary_password);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  return (
    <Dialog open={user !== null} onOpenChange={(open) => !open && close()}>
      <DialogContent dismissible={result === null}>
        {result === null ? (
          <>
            <div>
              <DialogTitle>Issue a one-time password</DialogTitle>
              <DialogDescription className="mt-1">
                This replaces the password for{" "}
                <strong>@{user?.username}</strong> with a generated temporary
                one, signs them out everywhere and forces a change at next sign
                in.
              </DialogDescription>
            </div>
            {problem !== null && <InlineProblem error={problem} />}
            <DialogFooter>
              <Button variant="outline" onClick={close}>
                Cancel
              </Button>
              <Button variant="destructive" loading={busy} onClick={issue}>
                <KeyRound className="size-4" strokeWidth={1.5} />
                Issue temporary password
              </Button>
            </DialogFooter>
          </>
        ) : (
          <>
            <div className="flex items-start gap-2">
              <ShieldAlert
                className="mt-0.5 size-5 shrink-0 text-warning"
                strokeWidth={1.5}
                aria-hidden="true"
              />
              <div>
                <DialogTitle>
                  Temporary password for @{result.username}
                </DialogTitle>
                <DialogDescription className="mt-1">
                  Shown once. Share it securely; it cannot be retrieved again.{" "}
                  <span className="tabular-nums">
                    {result.sessions_revoked}
                  </span>{" "}
                  session(s) revoked.
                </DialogDescription>
              </div>
            </div>
            <div className="flex items-center gap-2 rounded-md bg-foreground px-3 py-3">
              <code
                data-testid="temporary-password"
                className="tabular-nums flex-1 select-all break-all text-lg font-semibold tracking-wider text-background"
              >
                {result.temporary_password}
              </code>
              <Button variant="secondary" size="sm" onClick={copy}>
                {copied ? (
                  <Check className="size-4" strokeWidth={1.5} />
                ) : (
                  <Copy className="size-4" strokeWidth={1.5} />
                )}
                {copied ? "Copied" : "Copy"}
              </Button>
            </div>
            <DialogFooter>
              <Button onClick={close}>Done</Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

function QuotaCell({
  user,
  busy,
  onSave,
}: {
  user: AuthenticatedUser;
  busy: boolean;
  onSave: (limit: number) => void;
}) {
  const [value, setValue] = useState(String(user.hourly_generation_limit));
  const parsed = Number(value);
  const valid = Number.isInteger(parsed) && parsed >= 0 && parsed <= 1000;
  const dirty = value !== String(user.hourly_generation_limit);
  return (
    <div className="flex items-center gap-1.5">
      <Input
        type="number"
        min={0}
        max={1000}
        inputMode="numeric"
        aria-label={`Hourly limit for ${user.username}`}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        aria-invalid={!valid}
        className="tabular-nums h-7 w-16 px-2 text-xs"
      />
      {dirty && (
        <Button
          size="sm"
          className="h-7 px-2 text-xs"
          disabled={!valid || busy}
          onClick={() => onSave(parsed)}
          aria-label={`Save hourly limit for ${user.username}`}
        >
          Save
        </Button>
      )}
    </div>
  );
}

export function UsersPanel({ currentUserUuid }: { currentUserUuid: string }) {
  const [filter, setFilter] = useState<Filter>("ALL");
  const [busyUuid, setBusyUuid] = useState<string | null>(null);
  const [problem, setProblem] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [resetTarget, setResetTarget] = useState<AuthenticatedUser | null>(
    null,
  );

  const list = usePagedResource<AuthenticatedUser>(
    `users:${filter}`,
    (cursor, signal) =>
      api.admin.users(
        { cursor, limit: 25, status: filter === "ALL" ? undefined : filter },
        { signal },
      ),
  );

  async function patch(
    user: AuthenticatedUser,
    body: AdminUserUpdateRequest,
    message: string,
  ) {
    setBusyUuid(user.uuid);
    setProblem(null);
    setNotice(null);
    try {
      const updated = await api.admin.updateUser(user.uuid, body);
      list.update((items) =>
        items.map((u) => (u.uuid === updated.uuid ? updated : u)),
      );
      setNotice(message);
    } catch (err) {
      setProblem(err);
    } finally {
      setBusyUuid(null);
    }
  }

  async function revoke(user: AuthenticatedUser) {
    setBusyUuid(user.uuid);
    setProblem(null);
    setNotice(null);
    try {
      const r = await api.admin.revokeSessions(user.uuid);
      setNotice(
        `Revoked ${r.sessions_revoked} active session(s) for @${user.username}.`,
      );
    } catch (err) {
      setProblem(err);
    } finally {
      setBusyUuid(null);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <SegmentedControl<Filter>
        ariaLabel="Filter educators by status"
        size="sm"
        value={filter}
        onChange={setFilter}
        className="self-start"
        options={[
          { value: "ALL", label: "All" },
          { value: "ACTIVE", label: "Active" },
          { value: "PENDING", label: "Pending" },
          { value: "SUSPENDED", label: "Suspended" },
        ]}
      />
      {problem !== null && (
        <InlineProblem error={problem} onDismiss={() => setProblem(null)} />
      )}
      {notice && (
        <p
          role="status"
          className="m-0 rounded-md bg-success-bg px-3 py-2 text-xs font-medium text-success"
        >
          {notice}
        </p>
      )}
      <Surface>
        {list.loading ? (
          <TableSkeleton cols={6} />
        ) : list.error ? (
          <PanelError error={list.error} onRetry={list.reload} />
        ) : list.items.length === 0 ? (
          <PanelEmpty>No educators match this filter.</PanelEmpty>
        ) : (
          <Table aria-label="Educator accounts">
            <TableHeader>
              <TableRow>
                <TableHead>Educator</TableHead>
                <TableHead>Role</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Hourly limit</TableHead>
                <TableHead>Last sign in</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {list.items.map((u) => {
                const busy = busyUuid === u.uuid;
                const self = u.uuid === currentUserUuid;
                return (
                  <TableRow key={u.uuid}>
                    <TableCell>
                      <div className="font-semibold">{u.display_name}</div>
                      <div className="text-[11px] text-muted-foreground">
                        @{u.username}
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge variant={u.role === "ADMIN" ? "solid" : "neutral"}>
                        {u.role}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <Badge variant={STATUS_VARIANT[u.status]}>
                        {u.status}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <QuotaCell
                        key={`${u.uuid}:${u.hourly_generation_limit}`}
                        user={u}
                        busy={busy}
                        onSave={(limit) =>
                          patch(
                            u,
                            { hourly_generation_limit: limit },
                            `Updated limit for @${u.username}.`,
                          )
                        }
                      />
                    </TableCell>
                    <TableCell className="tabular-nums text-xs text-muted-foreground">
                      {formatUtc(u.last_login_at)}
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap justify-end gap-1">
                        {u.status === "PENDING" && (
                          <Button
                            size="sm"
                            className="h-7 text-xs"
                            disabled={busy}
                            onClick={() =>
                              patch(
                                u,
                                { status: "ACTIVE" },
                                `Approved @${u.username}.`,
                              )
                            }
                            aria-label={`Approve ${u.username}`}
                          >
                            Approve
                          </Button>
                        )}
                        {u.status === "ACTIVE" && !self && (
                          <Button
                            size="sm"
                            variant="outline"
                            className="h-7 text-xs"
                            disabled={busy}
                            onClick={() =>
                              patch(
                                u,
                                { status: "SUSPENDED" },
                                `Suspended @${u.username}.`,
                              )
                            }
                            aria-label={`Suspend ${u.username}`}
                          >
                            Suspend
                          </Button>
                        )}
                        {u.status === "SUSPENDED" && (
                          <Button
                            size="sm"
                            variant="outline"
                            className="h-7 text-xs"
                            disabled={busy}
                            onClick={() =>
                              patch(
                                u,
                                { status: "ACTIVE" },
                                `Reactivated @${u.username}.`,
                              )
                            }
                            aria-label={`Reactivate ${u.username}`}
                          >
                            Reactivate
                          </Button>
                        )}
                        {!self && (
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-7 text-xs"
                            disabled={busy}
                            onClick={() =>
                              patch(
                                u,
                                { role: u.role === "ADMIN" ? "USER" : "ADMIN" },
                                `${u.role === "ADMIN" ? "Demoted" : "Promoted"} @${u.username}.`,
                              )
                            }
                            aria-label={`${u.role === "ADMIN" ? "Demote" : "Promote"} ${u.username}`}
                          >
                            {u.role === "ADMIN" ? "Demote" : "Promote"}
                          </Button>
                        )}
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-7 text-xs"
                          disabled={busy}
                          onClick={() => revoke(u)}
                          aria-label={`Revoke sessions for ${u.username}`}
                        >
                          Revoke Sessions
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-7 text-xs text-danger hover:bg-danger-bg hover:text-danger"
                          disabled={busy}
                          onClick={() => setResetTarget(u)}
                          aria-label={`Reset password for ${u.username}`}
                        >
                          Reset Password
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
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
      <OneTimePasswordResetModal
        user={resetTarget}
        onClose={() => setResetTarget(null)}
        onIssued={list.reload}
      />
    </div>
  );
}
