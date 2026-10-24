import * as React from "react";
import { cn } from "@/lib/utils";

/** Monochrome vector crest: shield outline over three set-text bars. Inherits currentColor. */
export function Crest({ className, ...props }: React.ComponentProps<"svg">) {
  return (
    <svg
      viewBox="0 0 32 32"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinejoin="round"
      className={cn("size-8", className)}
      aria-hidden="true"
      {...props}
    >
      <path d="M16 2.5 4.5 6.5v8.2c0 7.3 4.6 12.2 11.5 14.8 6.9-2.6 11.5-7.5 11.5-14.8V6.5L16 2.5Z" />
      <path d="M10 11h9M10 15.5h7M10 20h12" strokeLinecap="round" />
    </svg>
  );
}

export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <Crest className="size-6 text-primary" />
      <span className="text-[15px] font-extrabold tracking-tight">
        Fulwood Forge
      </span>
    </span>
  );
}
