import { createContext, useContext, useState } from "react";
import { ChevronDown } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Md, Mdi } from "@/components/preview/Markdown";
import type { EditPath } from "@/lib/editPath";

/* Inline edit plumbing: every rendered text field is also its own editor. */

export interface EditApi {
  editing: boolean;
  set: (path: EditPath, value: string) => void;
}

export const EditContext = createContext<EditApi>({
  editing: false,
  set: () => undefined,
});

/** Section expand/collapse-all signal: `epoch` remounts sections with `allOpen`. */
export interface SectionsApi {
  allOpen: boolean;
  epoch: number;
}
export const SectionsContext = createContext<SectionsApi>({
  allOpen: true,
  epoch: 0,
});

/** Text that renders as Markdown normally and as an input/textarea when editing. */
export function MdField({
  path,
  value,
  label,
  inline = false,
  className,
}: {
  path: EditPath;
  value: string;
  label: string;
  inline?: boolean;
  className?: string;
}) {
  const { editing, set } = useContext(EditContext);
  if (!editing) {
    return inline ? (
      <Mdi className={className}>{value}</Mdi>
    ) : (
      <Md className={className}>{value}</Md>
    );
  }
  return inline ? (
    <Input
      aria-label={label}
      value={value}
      onChange={(e) => set(path, e.target.value)}
      className="h-8 min-w-48"
    />
  ) : (
    <Textarea
      aria-label={label}
      value={value}
      onChange={(e) => set(path, e.target.value)}
      className="font-mono text-xs"
    />
  );
}

/**
 * Per-item "Show / Hide Answer" toggle so a teacher on a smartboard can reveal a
 * single answer on demand. Hidden from print so a student copy never leaks it.
 */
export function AnswerReveal({
  path,
  answer,
  label,
}: {
  path: EditPath;
  answer: string | null | undefined;
  label: string;
}) {
  const [open, setOpen] = useState(false);
  const { editing } = useContext(EditContext);
  if (!answer) return null;
  return (
    <Collapsible
      open={open || editing}
      onOpenChange={setOpen}
      className="no-print mt-1.5"
      data-testid="answer-reveal"
    >
      <CollapsibleTrigger asChild>
        <Button variant="outline" size="sm" className="h-7 text-xs">
          {open ? "Hide answer" : "Show answer"}
          <ChevronDown
            className={`size-3.5 transition-transform ${open ? "rotate-180" : ""}`}
            strokeWidth={1.5}
          />
          <span className="sr-only"> for {label}</span>
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent className="mt-2 rounded-md border-l-[3px] border-success bg-success-bg/50 px-3 py-2">
        <MdField path={path} value={answer} label={`${label} answer`} />
      </CollapsibleContent>
    </Collapsible>
  );
}
