import * as React from "react";
import * as SwitchPrimitive from "@radix-ui/react-switch";
import { cn } from "@/lib/utils";

function Switch({
  className,
  ...props
}: React.ComponentProps<typeof SwitchPrimitive.Root>) {
  return (
    <SwitchPrimitive.Root
      className={cn(
        "fx-transition relative h-5 w-9 shrink-0 rounded-full bg-border-strong data-[state=checked]:bg-primary disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb className="fx-transition block size-4 translate-x-0.5 rounded-full bg-card data-[state=checked]:translate-x-[18px]" />
    </SwitchPrimitive.Root>
  );
}

export { Switch };
