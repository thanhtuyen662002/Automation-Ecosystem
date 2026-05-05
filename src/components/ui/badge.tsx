import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva("inline-flex items-center rounded-md px-2 py-1 text-xs font-medium", {
  variants: {
    variant: {
      default: "bg-muted text-foreground",
      success: "bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200",
      failed: "bg-red-50 text-red-700 ring-1 ring-red-200",
      running: "bg-blue-50 text-blue-700 ring-1 ring-blue-200",
      pending: "bg-slate-100 text-slate-700 ring-1 ring-slate-200",
      retry: "bg-orange-50 text-orange-700 ring-1 ring-orange-200",
    },
  },
  defaultVariants: {
    variant: "default",
  },
});

export function Badge({
  className,
  variant,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badgeVariants>) {
  return <span className={cn(badgeVariants({ variant, className }))} {...props} />;
}
