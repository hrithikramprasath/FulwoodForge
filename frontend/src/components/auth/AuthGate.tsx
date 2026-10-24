import { useEffect, useId, useState } from "react";
import { Clock, Info } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Crest } from "@/components/ui/crest";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { InlineProblem } from "@/components/preview/ErrorState";
import {
  PASSWORD_MIN,
  PasswordInput,
  PasswordMeter,
} from "@/components/auth/PasswordField";
import { api, ApiProblemError } from "@/lib/api";
import type { AuthStatusResponse } from "@/types/content";

type Mode = "signin" | "register";

/** Counts down a 429 lockout (REQ-AUTH-03); returns whole seconds remaining. */
function useCountdown(lockedUntil: number | null): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (lockedUntil === null) return;
    const id = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(id);
  }, [lockedUntil]);
  return lockedUntil === null
    ? 0
    : Math.max(0, Math.ceil((lockedUntil - now) / 1000));
}

export function AuthGate({
  status,
  onAuthenticated,
}: {
  status: AuthStatusResponse;
  /** Called after a session cookie has been issued; the shell re-reads /api/auth/status. */
  onAuthenticated: () => void;
}) {
  const uid = useId();
  const [mode, setMode] = useState<Mode>("signin");
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<unknown>(null);
  const [lockedUntil, setLockedUntil] = useState<number | null>(null);
  const [pendingUser, setPendingUser] = useState<string | null>(null);
  const lockSeconds = useCountdown(lockedUntil);

  const fieldErrors =
    problem instanceof ApiProblemError ? problem.invalidParams : {};
  const hasFieldErrors = Object.keys(fieldErrors).length > 0;
  const registering = mode === "register";
  const needsInvite = status.registration_mode === "INVITE_ONLY";

  function switchMode(next: string) {
    setMode(next as Mode);
    setProblem(null);
    setLockedUntil(null);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setProblem(null);
    try {
      if (registering) {
        const outcome = await api.auth.register({
          username: username.trim(),
          display_name: displayName.trim() || undefined,
          password,
          invite_code: needsInvite ? inviteCode.trim() || null : null,
        });
        if (outcome.pendingApproval) {
          setPendingUser(
            outcome.status.user?.username ?? username.trim().toLowerCase(),
          );
        } else {
          onAuthenticated();
        }
      } else {
        await api.auth.login({ username: username.trim(), password });
        onAuthenticated();
      }
    } catch (err) {
      setProblem(err);
      if (err instanceof ApiProblemError && err.status === 429) {
        setLockedUntil(Date.now() + (err.retryAfterSeconds ?? 60) * 1000);
      }
    } finally {
      setBusy(false);
    }
  }

  const locked = lockSeconds > 0;
  const canSubmit =
    username.trim().length >= 3 &&
    password.length > 0 &&
    (!registering || password.length >= PASSWORD_MIN) &&
    (!registering || !needsInvite || inviteCode.trim().length > 0);

  return (
    <div className="grid min-h-screen lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
      {/* Editorial brand panel */}
      <aside className="hidden flex-col justify-between bg-primary p-12 text-primary-foreground lg:flex">
        <div className="flex items-center gap-3">
          <Crest className="size-10" />
          <span className="text-xl font-extrabold tracking-tight">
            Fulwood Forge
          </span>
        </div>
        <div className="max-w-md">
          <p className="m-0 text-[11px] font-semibold uppercase tracking-[0.18em] text-primary-foreground/70">
            Fulwood Curriculum Studio
          </p>
          <h1 className="m-0 mt-3 text-4xl font-bold leading-tight tracking-tight">
            Exam-board quality resources, from a single idea.
          </h1>
          <p className="m-0 mt-4 text-sm leading-relaxed text-primary-foreground/80">
            Worksheets, slide decks and booklets built on the Fulwood lesson
            spine, with mark schemes and differentiation included.
          </p>
        </div>
        <p className="m-0 flex items-center gap-2 text-xs text-primary-foreground/80">
          <span
            className={`size-2 rounded-full ${status.maintenance_mode ? "bg-warning-bg" : "bg-success-bg"}`}
            aria-hidden="true"
          />
          Platform status:{" "}
          {status.maintenance_mode
            ? "Maintenance in progress"
            : "All systems operational"}
        </p>
      </aside>

      <main className="flex items-center justify-center p-6 sm:p-12">
        <div className="reveal flex w-full max-w-sm flex-col gap-5">
          <div className="flex items-center gap-2 lg:hidden">
            <Crest className="size-7 text-primary" />
            <span className="text-lg font-extrabold tracking-tight">
              Fulwood Forge
            </span>
          </div>

          {pendingUser ? (
            <section
              role="status"
              className="flex flex-col gap-3 rounded-lg border border-border bg-card p-6"
            >
              <Clock
                className="size-6 text-warning"
                strokeWidth={1.5}
                aria-hidden="true"
              />
              <h2 className="m-0 text-lg font-bold">
                Account Pending Administrator Approval
              </h2>
              <p className="m-0 text-[13px] text-text-secondary">
                Your educator account{" "}
                <span className="tabular-nums font-semibold">
                  @{pendingUser}
                </span>{" "}
                was created. An administrator must approve it before you can
                sign in.
              </p>
              <Button
                variant="outline"
                onClick={() => {
                  setPendingUser(null);
                  setPassword("");
                  switchMode("signin");
                }}
              >
                Back to sign in
              </Button>
            </section>
          ) : (
            <Tabs
              value={mode}
              onValueChange={switchMode}
              className="flex flex-col gap-5"
            >
              <TabsList className="w-full">
                <TabsTrigger value="signin">Sign In</TabsTrigger>
                <TabsTrigger value="register">
                  Create Educator Account
                </TabsTrigger>
              </TabsList>

              <TabsContent value={mode} className="flex flex-col gap-4">
                <div>
                  <h2 className="m-0 text-xl font-bold">
                    {registering
                      ? "Create your educator account"
                      : "Welcome back"}
                  </h2>
                  <p className="m-0 mt-1 text-[13px] text-muted-foreground">
                    {registering
                      ? "Choose a username and a password. No email address is needed."
                      : "Sign in with your username and password."}
                  </p>
                </div>

                {registering &&
                  status.registration_mode === "ADMIN_APPROVAL" && (
                    <p className="m-0 flex gap-2 rounded-md border border-border bg-accent px-3 py-2 text-xs text-accent-foreground">
                      <Info
                        className="mt-px size-4 shrink-0"
                        strokeWidth={1.5}
                        aria-hidden="true"
                      />
                      New accounts are held until an administrator approves
                      them.
                    </p>
                  )}

                <form
                  onSubmit={submit}
                  className="flex flex-col gap-3.5"
                  noValidate
                >
                  <Field
                    label="Username"
                    htmlFor={`${uid}-username`}
                    error={fieldErrors["username"]}
                  >
                    <Input
                      id={`${uid}-username`}
                      autoComplete="username"
                      autoCapitalize="none"
                      spellCheck={false}
                      value={username}
                      onChange={(e) => setUsername(e.target.value)}
                      aria-invalid={Boolean(fieldErrors["username"])}
                    />
                  </Field>

                  {registering && (
                    <Field
                      label="Display name (optional)"
                      htmlFor={`${uid}-display`}
                      error={fieldErrors["display_name"]}
                    >
                      <Input
                        id={`${uid}-display`}
                        autoComplete="name"
                        maxLength={64}
                        value={displayName}
                        onChange={(e) => setDisplayName(e.target.value)}
                      />
                    </Field>
                  )}

                  <Field
                    label="Password"
                    htmlFor={`${uid}-password`}
                    error={fieldErrors["password"]}
                  >
                    <PasswordInput
                      id={`${uid}-password`}
                      autoComplete={
                        registering ? "new-password" : "current-password"
                      }
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      aria-invalid={Boolean(fieldErrors["password"])}
                    />
                    {registering && <PasswordMeter password={password} />}
                  </Field>

                  {registering && needsInvite && (
                    <Field
                      label="Department / School Invite Code"
                      htmlFor={`${uid}-invite`}
                      error={fieldErrors["invite_code"]}
                    >
                      <Input
                        id={`${uid}-invite`}
                        autoCapitalize="characters"
                        spellCheck={false}
                        placeholder="e.g. FULWOOD-2026"
                        value={inviteCode}
                        onChange={(e) => setInviteCode(e.target.value)}
                        aria-invalid={Boolean(fieldErrors["invite_code"])}
                      />
                    </Field>
                  )}

                  {problem !== null && !hasFieldErrors && (
                    <InlineProblem error={problem} />
                  )}
                  {locked && (
                    <p
                      role="status"
                      className="m-0 text-xs font-medium text-warning"
                    >
                      Too many attempts. Try again in{" "}
                      <span className="tabular-nums font-bold">
                        {lockSeconds}
                      </span>{" "}
                      seconds.
                    </p>
                  )}

                  <Button
                    type="submit"
                    size="lg"
                    block
                    loading={busy}
                    disabled={!canSubmit || locked}
                  >
                    {registering ? "Create account" : "Sign in"}
                  </Button>
                </form>
              </TabsContent>
            </Tabs>
          )}
        </div>
      </main>
    </div>
  );
}
