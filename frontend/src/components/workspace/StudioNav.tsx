import { useState } from "react";
import { KeyRound, LogOut, TriangleAlert } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { Wordmark } from "@/components/ui/crest";
import { PasswordChangeDialog } from "@/components/auth/ForcedPasswordChangeModal";
import { api } from "@/lib/api";
import type { AuthenticatedUser } from "@/types/content";

export type StudioView = "studio" | "admin";

/** Top studio navigation bar plus the maintenance banner. */
export function StudioNav({
  user,
  maintenanceMode,
  view,
  onViewChange,
  generationsThisHour,
  onSignedOut,
  onUserUpdated,
}: {
  user: AuthenticatedUser;
  maintenanceMode: boolean;
  view: StudioView;
  onViewChange: (view: StudioView) => void;
  /** Count of generations this session, shown against the hourly quota. */
  generationsThisHour: number;
  onSignedOut: () => void;
  onUserUpdated: (user: AuthenticatedUser) => void;
}) {
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const isAdmin = user.role === "ADMIN";

  async function signOut() {
    setSigningOut(true);
    try {
      await api.auth.logout();
    } finally {
      setSigningOut(false);
      onSignedOut();
    }
  }

  return (
    <>
      {maintenanceMode && (
        <div
          role="status"
          className="no-print flex items-center justify-center gap-2 border-b border-warning/30 bg-warning-bg px-4 py-1.5 text-xs font-semibold text-warning"
        >
          <TriangleAlert
            className="size-4"
            strokeWidth={1.5}
            aria-hidden="true"
          />
          Maintenance mode is active. Generation is paused for educators.
        </div>
      )}
      <header
        data-app-chrome
        className="no-print flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border bg-card px-4 py-2.5"
      >
        <Wordmark />
        {isAdmin && (
          <SegmentedControl<StudioView>
            ariaLabel="Workspace view"
            size="sm"
            value={view}
            onChange={onViewChange}
            options={[
              { value: "studio", label: "Curriculum Studio" },
              { value: "admin", label: "Admin Control Center" },
            ]}
          />
        )}
        <div className="ml-auto flex flex-wrap items-center gap-3">
          <span
            className="tabular-nums rounded bg-muted px-2 py-1 text-xs font-semibold text-text-secondary"
            title="Generations this session against your hourly limit"
          >
            {generationsThisHour}/{user.hourly_generation_limit} per hour
          </span>
          <div className="flex items-center gap-2 text-right">
            <div className="leading-tight">
              <div className="text-[13px] font-semibold">
                {user.display_name}
              </div>
              <div className="text-[11px] text-muted-foreground">
                @{user.username}
              </div>
            </div>
            <Badge variant={isAdmin ? "solid" : "neutral"}>{user.role}</Badge>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPasswordOpen(true)}
          >
            <KeyRound className="size-4" strokeWidth={1.5} />
            Change Password
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={signOut}
            loading={signingOut}
          >
            <LogOut className="size-4" strokeWidth={1.5} />
            Sign Out
          </Button>
        </div>
      </header>
      <PasswordChangeDialog
        open={passwordOpen}
        onOpenChange={setPasswordOpen}
        onChanged={(updated) => {
          setPasswordOpen(false);
          onUserUpdated(updated);
        }}
      />
    </>
  );
}
