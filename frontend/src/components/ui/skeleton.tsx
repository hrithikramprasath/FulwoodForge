import * as React from "react";
import { cn } from "@/lib/utils";

/** Layout-matched placeholder block with the subtle luminance wave. */
function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      aria-hidden="true"
      className={cn("skeleton-shimmer rounded-md", className)}
      {...props}
    />
  );
}

export { Skeleton };
