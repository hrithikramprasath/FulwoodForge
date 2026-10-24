import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { InlineProblem } from "@/components/preview/ErrorState";
import { PanelError, Surface } from "@/components/admin/shared";
import { api, ApiProblemError } from "@/lib/api";
import { useResource } from "@/lib/useResource";
import {
  ALL_SUBJECTS,
  type RegistrationMode,
  type SubjectLiteral,
  type SystemSettings,
  type TaskType,
} from "@/types/content";

const TASKS: readonly { value: TaskType; label: string }[] = [
  { value: "worksheet", label: "Worksheet" },
  { value: "powerpoint", label: "PowerPoint" },
  { value: "booklet", label: "Booklet" },
];

function toggle<T>(list: readonly T[], item: T): T[] {
  return list.includes(item) ? list.filter((x) => x !== item) : [...list, item];
}

function SettingsForm({
  initial,
  onSaved,
}: {
  initial: SystemSettings;
  onSaved: (s: SystemSettings) => void;
}) {
  const uid = useId();
  const [draft, setDraft] = useState<SystemSettings>(initial);
  const [quota, setQuota] = useState(String(initial.default_hourly_quota));
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [problem, setProblem] = useState<unknown>(null);
  const fieldErrors =
    problem instanceof ApiProblemError ? problem.invalidParams : {};

  function change(next: Partial<SystemSettings>) {
    setDraft((d) => ({ ...d, ...next }));
    setSaved(false);
  }

  async function save() {
    setSaving(true);
    setProblem(null);
    try {
      const result = await api.admin.updateSettings({
        registration_mode: draft.registration_mode,
        default_hourly_quota: Number(quota),
        maintenance_mode: draft.maintenance_mode,
        allowed_subjects: draft.allowed_subjects,
        allowed_task_types: draft.allowed_task_types,
      });
      setDraft(result);
      setQuota(String(result.default_hourly_quota));
      setSaved(true);
      onSaved(result);
    } catch (err) {
      setProblem(err);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <Surface title="Registration and quota">
        <div className="flex flex-col gap-4 p-3">
          <Field
            label="Registration mode"
            error={fieldErrors["registration_mode"]}
          >
            <SegmentedControl<RegistrationMode>
              ariaLabel="Registration mode"
              size="sm"
              className="self-start"
              value={draft.registration_mode}
              onChange={(registration_mode) => change({ registration_mode })}
              options={[
                { value: "INVITE_ONLY", label: "Invite Only" },
                { value: "ADMIN_APPROVAL", label: "Admin Approval" },
                { value: "OPEN", label: "Open" },
              ]}
            />
          </Field>
          <Field
            label="Default hourly quota"
            htmlFor={`${uid}-quota`}
            error={fieldErrors["default_hourly_quota"]}
            className="w-40"
          >
            <Input
              id={`${uid}-quota`}
              type="number"
              min={0}
              max={1000}
              inputMode="numeric"
              className="tabular-nums"
              value={quota}
              onChange={(e) => {
                setQuota(e.target.value);
                setSaved(false);
              }}
              aria-invalid={Boolean(fieldErrors["default_hourly_quota"])}
            />
          </Field>
          <div className="flex items-center gap-3">
            <Switch
              id={`${uid}-maint`}
              checked={draft.maintenance_mode}
              onCheckedChange={(maintenance_mode) =>
                change({ maintenance_mode })
              }
              aria-label="Maintenance mode"
            />
            <label
              htmlFor={`${uid}-maint`}
              className="text-[13px] font-semibold"
            >
              Maintenance mode
              <span className="ml-2 font-normal text-muted-foreground">
                Pauses generation for educators.
              </span>
            </label>
          </div>
        </div>
      </Surface>
      <Surface title="Visible task types">
        <div className="flex flex-wrap gap-4 p-3">
          {TASKS.map((t) => (
            <label
              key={t.value}
              className="flex items-center gap-2 text-[13px]"
            >
              <Switch
                checked={draft.allowed_task_types.includes(t.value)}
                onCheckedChange={() =>
                  change({
                    allowed_task_types: toggle(
                      draft.allowed_task_types,
                      t.value,
                    ),
                  })
                }
                aria-label={`Allow ${t.label}`}
              />
              {t.label}
            </label>
          ))}
        </div>
      </Surface>
      <Surface title="Visible subjects">
        <div className="grid gap-x-4 gap-y-2 p-3 sm:grid-cols-2 lg:grid-cols-3">
          {ALL_SUBJECTS.map((subject: SubjectLiteral) => (
            <label
              key={subject}
              className="flex items-center gap-2 text-[13px]"
            >
              <Switch
                checked={draft.allowed_subjects.includes(subject)}
                onCheckedChange={() =>
                  change({
                    allowed_subjects: toggle(draft.allowed_subjects, subject),
                  })
                }
                aria-label={`Allow ${subject}`}
              />
              {subject}
            </label>
          ))}
        </div>
      </Surface>
      {problem !== null && (
        <InlineProblem error={problem} onDismiss={() => setProblem(null)} />
      )}
      <div className="flex items-center gap-3">
        <Button onClick={save} loading={saving}>
          Save Settings
        </Button>
        {saved && (
          <span role="status" className="text-xs font-semibold text-success">
            Settings saved.
          </span>
        )}
      </div>
    </div>
  );
}

/** Platform and curriculum visibility controls (GET/PUT /api/admin/settings). */
export function SettingsPanel() {
  const { data, error, loading, reload, setData } = useResource(
    "settings",
    (signal) => api.admin.settings({ signal }),
  );
  if (error) return <PanelError error={error} onRetry={reload} />;
  if (loading || !data) {
    return (
      <div
        className="flex flex-col gap-3"
        aria-busy="true"
        aria-label="Loading settings"
      >
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }
  return <SettingsForm initial={data} onSaved={setData} />;
}
