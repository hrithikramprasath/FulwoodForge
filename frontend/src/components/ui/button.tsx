import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { LoaderCircle } from "lucide-react";
import { cn } from "@/lib/utils";

/*
 * shadcn Button, React 19 style: `ref` is an ordinary prop, so there is no
 * forwardRef wrapper. Variants follow the Oxford Navy token constitution.
 */
const buttonVariants = cva(
  "fx-transition fx-press inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md text-[13px] font-semibold leading-tight select-none disabled:cursor-not-allowed disabled:opacity-50 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground hover:bg-primary-hover",
        secondary: "bg-secondary text-secondary-foreground hover:bg-border",
        outline:
          "border border-border-strong bg-card text-foreground hover:bg-muted",
        ghost: "text-text-secondary hover:bg-muted hover:text-foreground",
        destructive:
          "bg-destructive text-destructive-foreground hover:bg-destructive/90",
      },
      size: {
        sm: "h-8 px-3",
        default: "h-9 px-4",
        lg: "h-11 px-5 text-sm",
        icon: "size-9",
      },
      block: { true: "w-full", false: "" },
    },
    defaultVariants: { variant: "default", size: "default", block: false },
  },
);

export interface ButtonProps
  extends React.ComponentProps<"button">, VariantProps<typeof buttonVariants> {
  /** Shows an inline spinner and disables the control while true. */
  loading?: boolean;
}

function Button({
  className,
  variant,
  size,
  block,
  loading = false,
  disabled,
  children,
  type = "button",
  ...props
}: ButtonProps) {
  return (
    <button
      type={type}
      className={cn(buttonVariants({ variant, size, block }), className)}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...props}
    >
      {loading && (
        <LoaderCircle
          className="size-4 animate-spin motion-reduce:animate-none"
          strokeWidth={1.5}
          aria-hidden="true"
        />
      )}
      {children}
    </button>
  );
}

export { Button, buttonVariants };
