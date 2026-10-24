import { AuditLogPanel, TokenLedgerPanel } from "@/components/admin/AuditPanel";
import { InvitesPanel } from "@/components/admin/InvitesPanel";
import { OverviewRibbon } from "@/components/admin/OverviewRibbon";
import { SettingsPanel } from "@/components/admin/SettingsPanel";
import { UsersPanel } from "@/components/admin/UsersPanel";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

/** Admin Control Center (REQ-ADMIN-02): telemetry ribbon plus operational sub-views. */
export function AdminDashboard({
  currentUserUuid,
}: {
  currentUserUuid: string;
}) {
  return (
    <div className="mx-auto flex w-full max-w-7xl flex-col gap-4 p-4">
      <h1 className="m-0 text-xl">Admin Control Center</h1>
      <OverviewRibbon />
      <Tabs defaultValue="users" className="flex flex-col gap-3">
        <TabsList className="self-start">
          <TabsTrigger value="users">Educators</TabsTrigger>
          <TabsTrigger value="invites">Invite Codes</TabsTrigger>
          <TabsTrigger value="settings">Platform Settings</TabsTrigger>
          <TabsTrigger value="audit">Audit Log</TabsTrigger>
          <TabsTrigger value="ledger">Token Ledger</TabsTrigger>
        </TabsList>
        <TabsContent value="users">
          <UsersPanel currentUserUuid={currentUserUuid} />
        </TabsContent>
        <TabsContent value="invites">
          <InvitesPanel />
        </TabsContent>
        <TabsContent value="settings">
          <SettingsPanel />
        </TabsContent>
        <TabsContent value="audit">
          <AuditLogPanel />
        </TabsContent>
        <TabsContent value="ledger">
          <TokenLedgerPanel />
        </TabsContent>
      </Tabs>
    </div>
  );
}
