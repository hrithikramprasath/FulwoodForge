import { useEffect, useId } from "react";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  ABILITY_LABELS,
  EXAM_BOARDS,
  EXTENT_FIELDS,
  isGcseYear,
  TASK_LABELS,
  TOPIC_PLACEHOLDERS,
  YEAR_GROUPS,
} from "@/lib/adapt";
import { teacherRequestSchema, type TeacherRequestForm } from "@/schemas/form";
import type {
  Ability,
  SubjectLiteral,
  TaskType,
  TeacherRequest,
} from "@/types/content";

const ABILITIES: Ability[] = ["foundation", "mixed", "high"];
const TOPIC_MAX = 120;

export interface AdaptiveFormProps {
  allowedSubjects: readonly SubjectLiteral[];
  allowedTaskTypes: readonly TaskType[];
  onGenerate: (request: TeacherRequest) => void;
  /** RFC 9457 `invalid-params` from the last failed request, keyed by field name. */
  serverErrors?: Record<string, string>;
  disabled?: boolean;
  /** Preset topic chosen from the empty state; changing `presetKey` re-applies it. */
  preset?: {
    key: number;
    subject: string;
    year_group: string;
    topic: string;
    exam_board?: string;
  } | null;
}

/** Left-rail generator form. Options come only from the server-allowed lists. */
export function AdaptiveForm({
  allowedSubjects,
  allowedTaskTypes,
  onGenerate,
  serverErrors = {},
  disabled = false,
  preset = null,
}: AdaptiveFormProps) {
  const uid = useId();
  const firstTask = allowedTaskTypes[0] ?? "worksheet";
  const form = useForm<TeacherRequestForm>({
    resolver: zodResolver(teacherRequestSchema),
    mode: "onChange",
    defaultValues: {
      task_type: firstTask,
      subject: "",
      year_group: "9",
      topic: "",
      ability: "mixed",
      question_count:
        EXTENT_FIELDS[firstTask].name === "question_count" ? "10" : undefined,
      slide_count: undefined,
      booklet_length: undefined,
      include_answer_key: true,
    },
  });
  const presetKey = preset?.key;
  useEffect(() => {
    if (!preset) return;
    form.setValue("subject", preset.subject, { shouldValidate: true });
    form.setValue(
      "year_group",
      preset.year_group as TeacherRequestForm["year_group"],
      {
        shouldValidate: true,
      },
    );
    form.setValue("topic", preset.topic, { shouldValidate: true });
    form.setValue("exam_board", preset.exam_board, { shouldValidate: true });
    // Re-applied only when a different preset chip is chosen.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [presetKey]);

  const values = useWatch({ control: form.control }) as TeacherRequestForm;
  const errors = form.formState.errors;
  const extent = EXTENT_FIELDS[values.task_type ?? firstTask];
  const extentValue = values[extent.name];
  const gcse = isGcseYear(values.year_group);
  const topicLength = (values.topic ?? "").length;
  const err = (name: keyof TeacherRequestForm): string | undefined =>
    (errors[name]?.message as string | undefined) ?? serverErrors[name];

  function selectTask(task: TaskType) {
    form.setValue("task_type", task, { shouldValidate: true });
    form.setValue("question_count", undefined);
    form.setValue("slide_count", undefined);
    form.setValue("booklet_length", undefined);
    const field = EXTENT_FIELDS[task];
    const middle = field.options[Math.min(1, field.options.length - 1)].value;
    form.setValue(field.name, middle, { shouldValidate: true });
  }

  return (
    <form
      noValidate
      onSubmit={form.handleSubmit((v) => onGenerate(v as TeacherRequest))}
      className="flex flex-col gap-4"
      aria-label="Resource generator"
    >
      <Field label="Resource type" error={err("task_type")}>
        <SegmentedControl<TaskType>
          ariaLabel="Resource type"
          value={values.task_type}
          onChange={selectTask}
          options={allowedTaskTypes.map((t) => ({
            value: t,
            label: TASK_LABELS[t],
          }))}
          className="w-full"
        />
      </Field>

      <Field label="Subject" htmlFor={`${uid}-subject`} error={err("subject")}>
        <Select
          value={values.subject ?? ""}
          onValueChange={(s) =>
            form.setValue("subject", s, { shouldValidate: true })
          }
        >
          <SelectTrigger
            id={`${uid}-subject`}
            aria-label="Subject"
            aria-invalid={Boolean(err("subject"))}
          >
            <SelectValue placeholder="Choose a subject" />
          </SelectTrigger>
          <SelectContent>
            {allowedSubjects.map((s) => (
              <SelectItem key={s} value={s}>
                {s}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Field>

      <div className="grid grid-cols-2 gap-3">
        <Field
          label="Year group"
          htmlFor={`${uid}-year`}
          error={err("year_group")}
        >
          <Select
            value={values.year_group ?? ""}
            onValueChange={(y) => {
              form.setValue(
                "year_group",
                y as TeacherRequestForm["year_group"],
                {
                  shouldValidate: true,
                },
              );
              if (!isGcseYear(y)) form.setValue("exam_board", undefined);
            }}
          >
            <SelectTrigger id={`${uid}-year`} aria-label="Year group">
              <SelectValue placeholder="Year" />
            </SelectTrigger>
            <SelectContent>
              {YEAR_GROUPS.map((y) => (
                <SelectItem key={y} value={y}>
                  Year {y}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>

        <Field label="Tier" htmlFor={`${uid}-tier`} error={err("ability")}>
          <Select
            value={values.ability ?? ""}
            onValueChange={(a) =>
              form.setValue("ability", a as Ability, { shouldValidate: true })
            }
          >
            <SelectTrigger id={`${uid}-tier`} aria-label="Tier">
              <SelectValue placeholder="Tier" />
            </SelectTrigger>
            <SelectContent>
              {ABILITIES.map((a) => (
                <SelectItem key={a} value={a}>
                  {ABILITY_LABELS[a]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
      </div>

      {gcse && (
        <Field
          label="Exam board"
          htmlFor={`${uid}-board`}
          error={err("exam_board")}
        >
          <Select
            value={values.exam_board ?? ""}
            onValueChange={(b) =>
              form.setValue("exam_board", b, { shouldValidate: true })
            }
          >
            <SelectTrigger
              id={`${uid}-board`}
              aria-label="Exam board"
              aria-invalid={Boolean(err("exam_board"))}
            >
              <SelectValue placeholder="Choose an exam board" />
            </SelectTrigger>
            <SelectContent>
              {EXAM_BOARDS.map((b) => (
                <SelectItem key={b} value={b}>
                  {b}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
      )}

      <Field
        label="Topic"
        htmlFor={`${uid}-topic`}
        error={err("topic")}
        trailing={
          <span
            className={`tabular-nums text-[11px] ${topicLength > TOPIC_MAX ? "text-danger" : "text-muted-foreground"}`}
            aria-live="polite"
          >
            {topicLength}/{TOPIC_MAX}
          </span>
        }
      >
        <Textarea
          id={`${uid}-topic`}
          rows={3}
          {...form.register("topic")}
          placeholder={
            (values.subject && TOPIC_PLACEHOLDERS[values.subject]) ||
            "e.g. the quadratic formula"
          }
          aria-invalid={Boolean(err("topic"))}
          aria-describedby={err("topic") ? `${uid}-topic-error` : undefined}
        />
      </Field>

      <Field label={extent.question} error={err(extent.name)}>
        <SegmentedControl<string>
          ariaLabel={extent.question}
          value={extentValue}
          onChange={(v) =>
            form.setValue(extent.name, v, { shouldValidate: true })
          }
          options={extent.options.map((o) => ({
            value: o.value,
            label: o.label
              .split(" \u2014 ")[0]
              .split(" about ")[0]
              .replace(/ slides$/, ""),
          }))}
          className="w-full"
        />
        <p className="m-0 text-xs text-muted-foreground">
          {extent.options.find((o) => o.value === extentValue)?.label}
        </p>
      </Field>

      <div className="flex items-center justify-between gap-3 rounded-md border border-border bg-surface-subtle px-3 py-2">
        <div>
          <label
            htmlFor={`${uid}-answers`}
            className="text-[13px] font-semibold"
          >
            Include teacher answer key
          </label>
          <p className="m-0 text-xs text-muted-foreground">
            Worked solutions and mark scheme.
          </p>
        </div>
        <Switch
          id={`${uid}-answers`}
          checked={values.include_answer_key ?? false}
          onCheckedChange={(c) => form.setValue("include_answer_key", c)}
        />
      </div>

      <Button type="submit" size="lg" block disabled={disabled}>
        <Sparkles className="size-4" strokeWidth={1.5} />
        Generate {TASK_LABELS[values.task_type ?? firstTask].toLowerCase()}
      </Button>
    </form>
  );
}
