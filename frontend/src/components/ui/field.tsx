import * as React from "react";
import { cn } from "@/lib/utils";

/** Label + control + inline RFC 9457 `invalid-params` message. */
function Field({
  label,
  htmlFor,
  error,
  hint,
  className,
  children,
  trailing,
}: {
  label: string;
  htmlFor?: string;
  error?: string | null;
  hint?: string;
  className?: string;
  trailing?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <div className="flex items-baseline justify-between gap-2">
        <label
          htmlFor={htmlFor}
          className="text-xs font-semibold text-text-secondary"
        >
          {label}
        </label>
        {trailing}
      </div>
      {children}
      {error ? (
        <p
          id={htmlFor ? `${htmlFor}-error` : undefined}
          role="alert"
          className="m-0 text-xs font-medium text-danger"
        >
          {error}
        </p>
      ) : hint ? (
        <p className="m-0 text-xs text-muted-foreground">{hint}</p>
      ) : null}
    </div>
  );
}

export { Field };
