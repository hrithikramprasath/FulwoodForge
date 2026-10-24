import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogTitle,
} from "@/components/ui/dialog";
import { Field } from "@/components/ui/field";
import { InlineProblem } from "@/components/preview/ErrorState";
import {
  PASSWORD_MIN,
  PasswordInput,
  PasswordMeter,
} from "@/components/auth/PasswordField";
import { api, ApiProblemError } from "@/lib/api";
import type { AuthenticatedUser } from "@/types/content";

/**
 * Password change dialog. With `forced` (REQ-AUTH-06, `must_change_password`) it is
 * non-dismissible: no close button, no Escape, no outside click, so the workspace
 * stays locked until a new personal password is set.
 */
export function PasswordChangeDialog({
  open,
  forced = false,
  onOpenChange,
  onChanged,
}: {
  open: boolean;
  forced?: boolean;
  onOpenChange?: (open: boolean) => void;
  onChanged: (user: AuthenticatedUser) => void;
}) {
  const uid = useId();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<unknown>(null);

  const fieldErrors =
    problem instanceof ApiProblemError ? problem.invalidParams : {};
  const mismatch =
    confirm.length > 0 && confirm !== next ? "Passwords do not match." : null;
  const valid =
    current.length > 0 && next.length >= PASSWORD_MIN && next === confirm;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!valid) return;
    setBusy(true);
    setProblem(null);
    try {
      const user = await api.auth.changePassword({
        current_password: current,
        new_password: next,
      });
      setCurrent("");
      setNext("");
      setConfirm("");
      onChanged(user);
    } catch (err) {
      setProblem(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={forced ? undefined : onOpenChange}>
      <DialogContent dismissible={!forced} aria-describedby={`${uid}-desc`}>
        <div>
          <DialogTitle>
            {forced ? "Set a new password" : "Change password"}
          </DialogTitle>
          <DialogDescription id={`${uid}-desc`} className="mt-1">
            {forced
              ? "An administrator issued you a temporary password. Choose a personal password to continue."
              : "Choose a new password of 10 to 128 characters. Other sessions are signed out."}
          </DialogDescription>
        </div>
        <form onSubmit={submit} className="flex flex-col gap-3" noValidate>
          <Field
            label={forced ? "Temporary password" : "Current password"}
            htmlFor={`${uid}-current`}
            error={fieldErrors["current_password"]}
          >
            <PasswordInput
              id={`${uid}-current`}
              autoComplete="current-password"
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
              aria-invalid={Boolean(fieldErrors["current_password"])}
            />
          </Field>
          <Field
            label="New password"
            htmlFor={`${uid}-new`}
            error={fieldErrors["new_password"]}
          >
            <PasswordInput
              id={`${uid}-new`}
              autoComplete="new-password"
              value={next}
              onChange={(e) => setNext(e.target.value)}
              aria-invalid={Boolean(fieldErrors["new_password"])}
            />
            <PasswordMeter password={next} />
          </Field>
          <Field
            label="Confirm new password"
            htmlFor={`${uid}-confirm`}
            error={mismatch}
          >
            <PasswordInput
              id={`${uid}-confirm`}
              autoComplete="new-password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              aria-invalid={Boolean(mismatch)}
            />
          </Field>
          {problem !== null && Object.keys(fieldErrors).length === 0 && (
            <InlineProblem error={problem} />
          )}
          <DialogFooter>
            {!forced && (
              <Button variant="outline" onClick={() => onOpenChange?.(false)}>
                Cancel
              </Button>
            )}
            <Button type="submit" loading={busy} disabled={!valid}>
              Update password
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** Mandatory variant mounted when `user.must_change_password === true`. */
export function ForcedPasswordChangeModal({
  onChanged,
}: {
  onChanged: (user: AuthenticatedUser) => void;
}) {
  return <PasswordChangeDialog open forced onChanged={onChanged} />;
}
