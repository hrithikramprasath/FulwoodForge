import * as React from "react";
import { cn } from "@/lib/utils";

const FIELD_BASE =
  "fx-transition w-full rounded-md border border-input bg-card px-3 text-[13px] text-foreground caret-primary placeholder:text-muted-foreground hover:border-border-strong focus-visible:border-ring focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-ring disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-danger";

/** Crisp 1px input, 2px focus ring; `ref` flows through as a React 19 prop. */
function Input({ className, ...props }: React.ComponentProps<"input">) {
  return <input className={cn(FIELD_BASE, "h-9", className)} {...props} />;
}

export { Input, FIELD_BASE };
