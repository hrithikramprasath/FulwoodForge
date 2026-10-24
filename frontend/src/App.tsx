import { useCallback, useEffect, useState } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { AdminDashboard } from "@/components/admin/AdminDashboard";
import { AuthGate } from "@/components/auth/AuthGate";
import { ForcedPasswordChangeModal } from "@/components/auth/ForcedPasswordChangeModal";
import { InlineProblem } from "@/components/preview/ErrorState";
import { StudioNav, type StudioView } from "@/components/workspace/StudioNav";
import { StudioWorkspace } from "@/components/workspace/StudioWorkspace";
import { api, isAbortError } from "@/lib/api";
import type { AuthStatusResponse } from "@/types/content";

/*
 * Application shell: native session gate (REQ-AUTH-01..06) -> forced password
 * change -> Curriculum Studio or Admin Control Center. All network access goes
 * through lib/api.ts.
 */
type Boot =
  | { name: "loading" }
  | { name: "error"; error: unknown }
  | { name: "ready"; status: AuthStatusResponse };

export default function App() {
  const [boot, setBoot] = useState<Boot>({ name: "loading" });
  const [view, setView] = useState<StudioView>("studio");
  const [generations, setGenerations] = useState(0);
  const [attempt, setAttempt] = useState(0);

  const refresh = useCallback(() => setAttempt((n) => n + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    api.auth
      .status({ signal: controller.signal })
      .then((status) => setBoot({ name: "ready", status }))
      .catch((error: unknown) => {
        if (!isAbortError(error)) setBoot({ name: "error", error });
      });
    return () => controller.abort();
  }, [attempt]);

  if (boot.name === "loading") {
    return (
      <div
        className="flex min-h-screen flex-col gap-4 p-6"
        aria-busy="true"
        aria-label="Loading"
      >
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-96 w-full" />
      </div>
    );
  }

  if (boot.name === "error") {
    return (
      <div className="mx-auto flex min-h-screen max-w-md flex-col items-start justify-center gap-3 p-6">
        <InlineProblem error={boot.error} />
        <Button
          variant="outline"
          onClick={() => {
            setBoot({ name: "loading" });
            refresh();
          }}
        >
          Retry
        </Button>
      </div>
    );
  }

  const { status } = boot;
  const user = status.authenticated ? (status.user ?? null) : null;

  if (!user) {
    return <AuthGate status={status} onAuthenticated={refresh} />;
  }

  return (
    <div className="flex min-h-screen flex-col">
      <StudioNav
        user={user}
        maintenanceMode={status.maintenance_mode}
        view={user.role === "ADMIN" ? view : "studio"}
        onViewChange={setView}
        generationsThisHour={generations}
        onSignedOut={() => {
          setGenerations(0);
          setView("studio");
          refresh();
        }}
        onUserUpdated={refresh}
      />
      {user.must_change_password ? (
        <ForcedPasswordChangeModal onChanged={refresh} />
      ) : user.role === "ADMIN" && view === "admin" ? (
        <AdminDashboard currentUserUuid={user.uuid} />
      ) : (
        <StudioWorkspace
          allowedSubjects={status.allowed_subjects}
          allowedTaskTypes={status.allowed_task_types}
          maintenanceMode={status.maintenance_mode}
          onGenerated={() => setGenerations((n) => n + 1)}
        />
      )}
      <footer className="no-print border-t border-border">
        <p className="m-0 px-4 py-3 text-[11px] text-muted-foreground">
          AI can make mistakes. Read through anything you generate before using
          it with a class.
        </p>
      </footer>
    </div>
  );
}
