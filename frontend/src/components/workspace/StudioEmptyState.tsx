import { Library } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { SubjectLiteral } from "@/types/content";

export interface StudioPreset {
  key: number;
  label: string;
  subject: SubjectLiteral;
  year_group: string;
  exam_board?: string;
  topic: string;
}

const PRESETS: Omit<StudioPreset, "key">[] = [
  {
    label: "AQA GCSE Maths: Quadratic Formula",
    subject: "Mathematics",
    year_group: "10",
    exam_board: "AQA",
    topic: "The quadratic formula",
  },
  {
    label: "AQA Combined Science: Osmosis",
    subject: "Combined Science",
    year_group: "10",
    exam_board: "AQA",
    topic: "Osmosis",
  },
  {
    label: "Edexcel Computer Science: Binary & Hexadecimal",
    subject: "Computer Science",
    year_group: "11",
    exam_board: "Edexcel",
    topic: "Binary and hexadecimal",
  },
];

/** Architectural blueprint drawing: a lesson sheet with construction lines. */
function Blueprint() {
  return (
    <svg
      viewBox="0 0 240 160"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      className="w-56 text-border-strong"
      aria-hidden="true"
    >
      <rect
        x="62"
        y="12"
        width="116"
        height="140"
        rx="3"
        className="text-muted-foreground"
      />
      <path
        d="M78 34h56M78 46h84M78 58h72"
        strokeLinecap="round"
        className="text-primary"
      />
      <rect x="78" y="72" width="84" height="34" rx="2" strokeDasharray="4 3" />
      <path d="M78 119h34M78 131h60" strokeLinecap="round" />
      <path
        d="M12 24h40M12 80h40M12 136h40"
        strokeDasharray="2 4"
        strokeLinecap="round"
      />
      <path
        d="M188 24h40M188 80h40M188 136h40"
        strokeDasharray="2 4"
        strokeLinecap="round"
      />
      <circle cx="170" cy="130" r="14" className="text-primary" />
      <path
        d="m164 130 4 4 8-9"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="text-primary"
      />
    </svg>
  );
}

/** State 2: zero-data studio canvas. */
export function StudioEmptyState({
  allowedSubjects,
  onPreset,
  onOpenLibrary,
}: {
  allowedSubjects: readonly SubjectLiteral[];
  onPreset: (preset: StudioPreset) => void;
  onOpenLibrary: () => void;
}) {
  const presets = PRESETS.filter((p) => allowedSubjects.includes(p.subject));
  return (
    <section
      aria-label="Empty studio canvas"
      className="reveal flex min-h-96 flex-col items-center justify-center gap-4 rounded-lg border border-dashed border-border-strong bg-card px-6 py-12 text-center"
    >
      <Blueprint />
      <div>
        <h2 className="m-0 text-lg font-bold">
          Your curriculum canvas is empty
        </h2>
        <p className="m-0 mt-1 max-w-md text-[13px] text-muted-foreground">
          Describe a lesson in the studio rail, or start from a
          specification-aligned example.
        </p>
      </div>
      {presets.length > 0 && (
        <ul className="m-0 flex list-none flex-wrap justify-center gap-2 p-0">
          {presets.map((p, i) => (
            <li key={p.label}>
              <button
                type="button"
                onClick={() => onPreset({ ...p, key: Date.now() + i })}
                className="fx-transition fx-press rounded-full border border-border-strong bg-card px-3 py-1.5 text-xs font-semibold text-text-secondary hover:border-primary hover:bg-accent hover:text-accent-foreground"
              >
                {p.label}
              </button>
            </li>
          ))}
        </ul>
      )}
      <Button variant="outline" onClick={onOpenLibrary}>
        <Library className="size-4" strokeWidth={1.5} />
        Open Saved Library
      </Button>
    </section>
  );
}
