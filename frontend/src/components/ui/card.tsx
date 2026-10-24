import * as React from "react";
import { cn } from "@/lib/utils";

function Card({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      className={cn(
        "rounded-lg border border-border bg-card text-card-foreground",
        className,
      )}
      {...props}
    />
  );
}

/** Small uppercase navy overline above a card title. */
function CardKicker({ className, ...props }: React.ComponentProps<"p">) {
  return (
    <p
      className={cn(
        "m-0 text-[10px] font-semibold uppercase tracking-[0.12em] text-primary",
        className,
      )}
      {...props}
    />
  );
}

function CardTitle({ className, ...props }: React.ComponentProps<"h2">) {
  return (
    <h2
      className={cn("m-0 text-lg font-bold text-foreground", className)}
      {...props}
    />
  );
}

function CardContent({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("flex flex-col gap-4", className)} {...props} />;
}

export { Card, CardKicker, CardTitle, CardContent };
