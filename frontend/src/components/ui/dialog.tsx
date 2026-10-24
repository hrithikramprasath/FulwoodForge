import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

const Dialog = DialogPrimitive.Root;
const DialogTrigger = DialogPrimitive.Trigger;
const DialogClose = DialogPrimitive.Close;

/**
 * Modal with focus trap, scroll lock and Escape handling from Radix. Set
 * `dismissible={false}` for a mandatory flow (forced password change): Escape,
 * outside clicks and the close button are all disabled.
 */
function DialogContent({
  className,
  children,
  dismissible = true,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Content> & {
  dismissible?: boolean;
}) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="dialog-overlay fixed inset-0 z-50 bg-foreground/40 backdrop-blur-[2px]" />
      <DialogPrimitive.Content
        onEscapeKeyDown={dismissible ? undefined : (e) => e.preventDefault()}
        onPointerDownOutside={
          dismissible ? undefined : (e) => e.preventDefault()
        }
        onInteractOutside={dismissible ? undefined : (e) => e.preventDefault()}
        className={cn(
          "dialog-content fixed left-1/2 top-1/2 z-50 flex max-h-[90vh] w-[calc(100%-2rem)] max-w-md flex-col gap-4 overflow-y-auto rounded-lg border border-border bg-popover p-6 shadow-popover",
          className,
        )}
        {...props}
      >
        {children}
        {dismissible && (
          <DialogPrimitive.Close
            aria-label="Close dialog"
            className="fx-transition absolute right-3 top-3 rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <X className="size-4" strokeWidth={1.5} />
          </DialogPrimitive.Close>
        )}
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}

function DialogTitle({
  className,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Title>) {
  return (
    <DialogPrimitive.Title
      className={cn(
        "m-0 text-base font-bold tracking-tight text-foreground",
        className,
      )}
      {...props}
    />
  );
}

function DialogDescription({
  className,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Description>) {
  return (
    <DialogPrimitive.Description
      className={cn("m-0 text-[13px] text-muted-foreground", className)}
      {...props}
    />
  );
}

function DialogFooter({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      className={cn("flex flex-wrap justify-end gap-2", className)}
      {...props}
    />
  );
}

export {
  Dialog,
  DialogTrigger,
  DialogClose,
  DialogContent,
  DialogTitle,
  DialogDescription,
  DialogFooter,
};
