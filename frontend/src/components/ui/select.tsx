import * as React from "react";
import * as SelectPrimitive from "@radix-ui/react-select";
import { Check, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

/*
 * Accessible custom Select (Radix headless primitive) replacing the native OS
 * <select>: keyboard navigation, check indicator, chevron, and an `aria-invalid`
 * field-error state.
 */
const Select = SelectPrimitive.Root;
const SelectValue = SelectPrimitive.Value;

function SelectTrigger({
  className,
  children,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Trigger>) {
  return (
    <SelectPrimitive.Trigger
      className={cn(
        "fx-transition flex h-9 w-full items-center justify-between gap-2 rounded-md border border-input bg-card px-3 text-left text-[13px] text-foreground",
        "hover:border-border-strong data-[state=open]:border-ring data-[placeholder]:text-muted-foreground",
        "aria-invalid:border-danger disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    >
      {children}
      <SelectPrimitive.Icon asChild>
        <ChevronDown
          className="size-4 text-muted-foreground"
          strokeWidth={1.5}
        />
      </SelectPrimitive.Icon>
    </SelectPrimitive.Trigger>
  );
}

function SelectContent({
  className,
  children,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Content>) {
  return (
    <SelectPrimitive.Portal>
      <SelectPrimitive.Content
        position="popper"
        sideOffset={4}
        className={cn(
          "z-50 max-h-72 w-[var(--radix-select-trigger-width)] overflow-y-auto rounded-md border border-border bg-popover shadow-popover",
          className,
        )}
        {...props}
      >
        <SelectPrimitive.Viewport className="p-1">
          {children}
        </SelectPrimitive.Viewport>
      </SelectPrimitive.Content>
    </SelectPrimitive.Portal>
  );
}

function SelectItem({
  className,
  children,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Item>) {
  return (
    <SelectPrimitive.Item
      className={cn(
        "flex cursor-pointer select-none items-center justify-between gap-2 rounded px-2.5 py-2 text-[13px] text-foreground outline-none",
        "data-[highlighted]:bg-accent data-[highlighted]:text-accent-foreground data-[state=checked]:font-semibold",
        className,
      )}
      {...props}
    >
      <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
      <SelectPrimitive.ItemIndicator>
        <Check className="size-4 text-primary" strokeWidth={1.5} />
      </SelectPrimitive.ItemIndicator>
    </SelectPrimitive.Item>
  );
}

export { Select, SelectValue, SelectTrigger, SelectContent, SelectItem };
