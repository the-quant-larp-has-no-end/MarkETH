import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { Slot } from "radix-ui";

import { cn } from "@/lib/utils";

/**
 * The badge of DESIGN.md §12: `caption` text in capitals, `--radius-sm`, 4 x 8 px
 * padding. Green and red exist only as the soft tints of a resolved outcome.
 */
const badgeVariants = cva(
  "inline-flex w-fit shrink-0 items-center justify-center gap-1 rounded-sm px-2 py-1 type-caption whitespace-nowrap uppercase [&>svg]:pointer-events-none [&>svg]:size-3",
  {
    variants: {
      variant: {
        /** `--raised` fill, white text. */
        raised: "bg-raised text-foreground",
        /** `--raised` fill, muted text. */
        muted: "bg-raised text-muted-foreground",
        /** A 1 px `--border-strong` outline, white text. */
        outline: "border border-border-strong text-foreground",
        yes: "bg-yes-soft text-yes-text",
        no: "bg-no-soft text-no-text",
      },
    },
    defaultVariants: {
      variant: "raised",
    },
  },
);

function Badge({
  className,
  variant = "raised",
  asChild = false,
  ...props
}: React.ComponentProps<"span"> & VariantProps<typeof badgeVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot.Root : "span";

  return (
    <Comp
      data-slot="badge"
      data-variant={variant}
      className={cn(badgeVariants({ variant }), className)}
      {...props}
    />
  );
}

export { Badge, badgeVariants };
