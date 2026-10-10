import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

/**
 * The surface of a banner (DESIGN.md §12): a condition that lasts, full width inside the
 * content column, with an icon. Neutral is `--raised`; an error is `--no-soft` with
 * `--no-text`. Screens use `Banner` and `ErrorBanner` from `@/components/feedback`.
 */
const alertVariants = cva(
  "flex w-full items-start gap-3 rounded-md px-4 py-3 type-body-sm [&>svg]:mt-px [&>svg]:size-5 [&>svg]:shrink-0",
  {
    variants: {
      variant: {
        neutral: "bg-raised text-foreground",
        error: "bg-no-soft text-no-text",
      },
    },
    defaultVariants: {
      variant: "neutral",
    },
  },
);

function Alert({
  className,
  variant = "neutral",
  role = "status",
  ...props
}: React.ComponentProps<"div"> & VariantProps<typeof alertVariants>) {
  return (
    <div
      data-slot="alert"
      data-variant={variant}
      role={role}
      className={cn(alertVariants({ variant }), className)}
      {...props}
    />
  );
}

function AlertTitle({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="alert-title" className={cn("font-semibold", className)} {...props} />;
}

function AlertDescription({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="alert-description"
      className={cn("min-w-0 flex-1 wrap-break-word", className)}
      {...props}
    />
  );
}

export { Alert, AlertDescription, AlertTitle };
