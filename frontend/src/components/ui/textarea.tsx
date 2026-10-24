import * as React from "react";
import { cn } from "@/lib/utils";
import { FIELD_BASE } from "@/components/ui/input";

function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return (
    <textarea
      className={cn(
        FIELD_BASE,
        "min-h-20 resize-y py-2 leading-relaxed",
        className,
      )}
      {...props}
    />
  );
}

export { Textarea };
