import { useRef } from "react";
import { cn } from "@/lib/utils";

/*
 * Tactile segmented control implementing the WAI-ARIA radio group pattern:
 * one tab stop (roving tabindex), arrow keys move and select.
 */
export interface SegmentedOption<T extends string> {
  value: T;
  label: string;
  /** Optional trailing count rendered with tabular figures. */
  count?: number;
  disabled?: boolean;
}

interface SegmentedControlProps<T extends string> {
  options: readonly SegmentedOption<T>[];
  value: T | undefined;
  onChange: (value: T) => void;
  ariaLabel: string;
  size?: "sm" | "default";
  className?: string;
}

export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
  size = "default",
  className,
}: SegmentedControlProps<T>) {
  const buttonsRef = useRef<(HTMLButtonElement | null)[]>([]);

  function moveTo(from: number, step: 1 | -1) {
    for (let i = 1; i <= options.length; i++) {
      const index = (from + step * i + options.length * i) % options.length;
      const next = options[index];
      if (!next.disabled) {
        onChange(next.value);
        buttonsRef.current[index]?.focus();
        return;
      }
    }
  }

  function handleKeyDown(event: React.KeyboardEvent, index: number) {
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      event.preventDefault();
      moveTo(index, 1);
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      event.preventDefault();
      moveTo(index, -1);
    }
  }

  const tabbable = options.findIndex((o) => o.value === value);

  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      className={cn("inline-flex gap-0.5 rounded-md bg-muted p-0.5", className)}
    >
      {options.map((opt, i) => {
        const selected = opt.value === value;
        return (
          <button
            key={opt.value}
            ref={(el) => {
              buttonsRef.current[i] = el;
            }}
            type="button"
            role="radio"
            aria-checked={selected}
            disabled={opt.disabled}
            tabIndex={selected || (tabbable === -1 && i === 0) ? 0 : -1}
            onClick={() => onChange(opt.value)}
            onKeyDown={(e) => handleKeyDown(e, i)}
            className={cn(
              "fx-transition fx-press inline-flex flex-1 items-center justify-center gap-1.5 whitespace-nowrap rounded font-semibold disabled:cursor-not-allowed disabled:opacity-50",
              size === "sm" ? "px-2.5 py-1 text-xs" : "px-3 py-1.5 text-[13px]",
              selected
                ? "bg-card text-primary shadow-[0_0_0_1px_var(--border-subtle)]"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {opt.label}
            {opt.count !== undefined && (
              <span className="tabular-nums rounded bg-accent px-1 text-[10px] text-accent-foreground">
                {opt.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
