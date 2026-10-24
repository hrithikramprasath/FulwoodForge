import * as React from "react";
import { cn } from "@/lib/utils";

/** Linear-style high-density table primitives. */
function Table({ className, ...props }: React.ComponentProps<"table">) {
  return (
    <div className="w-full overflow-x-auto">
      <table
        className={cn("w-full border-collapse text-[13px]", className)}
        {...props}
      />
    </div>
  );
}
function TableHeader({ className, ...props }: React.ComponentProps<"thead">) {
  return <thead className={cn("bg-surface-subtle", className)} {...props} />;
}
function TableBody({ className, ...props }: React.ComponentProps<"tbody">) {
  return <tbody className={className} {...props} />;
}
function TableRow({ className, ...props }: React.ComponentProps<"tr">) {
  return (
    <tr
      className={cn(
        "fx-transition border-b border-border hover:bg-surface-subtle",
        className,
      )}
      {...props}
    />
  );
}
function TableHead({ className, ...props }: React.ComponentProps<"th">) {
  return (
    <th
      className={cn(
        "h-9 whitespace-nowrap px-3 text-left text-[11px] font-semibold uppercase tracking-wider text-muted-foreground",
        className,
      )}
      {...props}
    />
  );
}
function TableCell({ className, ...props }: React.ComponentProps<"td">) {
  return <td className={cn("px-3 py-2 align-middle", className)} {...props} />;
}

export { Table, TableHeader, TableBody, TableRow, TableHead, TableCell };
