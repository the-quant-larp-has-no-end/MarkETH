import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { LoaderCircleIcon } from "lucide-react";
import { Slot } from "radix-ui";

import { cn } from "@/lib/utils";

/**
 * The button of DESIGN.md §12: 48 px tall (56 px for the trade's Confirm), `--radius-md`,
 * the `label` text style, full width on phones.
 *
 * Variants: `primary` is the one main action of a screen, `secondary` any other action,
 * `ghost` a low-emphasis one ("Cancel"), `destructive` goes behind a confirmation
 * dialog, and `yes` / `no` are the outcome fills, which also carry the Confirm of a buy.
 * Labels on green and red are `--ink`, never white.
 */
const buttonVariants = cva(
  [
    "relative inline-flex shrink-0 items-center justify-center gap-2 rounded-md type-label whitespace-nowrap select-none",
    "transition-[background-color,border-color,color,opacity] duration-(--duration-fast)",
    "disabled:pointer-events-none aria-disabled:pointer-events-none",
    "[&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-5",
  ],
  {
    variants: {
      variant: {
        primary:
          "bg-primary text-primary-foreground hover:bg-primary/90 active:bg-primary/90 disabled:opacity-50",
        secondary:
          "border border-border-strong text-foreground hover:bg-raised active:bg-raised disabled:border-subtle disabled:text-subtle",
        ghost: "text-foreground hover:bg-raised active:bg-raised disabled:text-subtle",
        destructive: "bg-no text-ink hover:bg-no/90 active:bg-no/90 disabled:opacity-50",
        yes: "bg-yes text-ink hover:bg-yes/90 active:bg-yes/90 disabled:opacity-50",
        no: "bg-no text-ink hover:bg-no/90 active:bg-no/90 disabled:opacity-50",
      },
      size: {
        /** 48 px. */
        default: "h-12 px-4",
        /** 56 px: the Confirm button of the trade sheet. */
        confirm: "h-14 px-4",
        /** 44 px: the dense rows of the admin area. */
        compact: "h-11 px-3",
        /** A 44 px square for a control that is an icon; it needs an `aria-label`. */
        icon: "size-11",
      },
      width: {
        /**
         * Full width on phones, as wide as its label from 768 px. The width is its own
         * there, so a column that stretches its children does not stretch the button.
         */
        responsive: "w-full md:w-fit",
        /** Full width at every size: the action of a sheet or a dialog. */
        full: "w-full",
        /** As wide as its label, also in a column that stretches its children. */
        fit: "w-fit",
      },
    },
    defaultVariants: {
      variant: "primary",
      size: "default",
      width: "responsive",
    },
  },
);

type ButtonProps = React.ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean;
    /**
     * Waiting for its response: the button keeps its width, shows a spinner in place of
     * its label and is disabled. Not for `asChild`.
     */
    loading?: boolean;
  };

function Button({
  className,
  variant = "primary",
  size = "default",
  width,
  asChild = false,
  loading = false,
  disabled,
  children,
  type,
  ...props
}: ButtonProps) {
  // An icon button is a square: its size is its width.
  const resolvedWidth = size === "icon" ? null : (width ?? "responsive");
  const classes = cn(buttonVariants({ variant, size, width: resolvedWidth }), className);

  if (asChild) {
    return (
      <Slot.Root data-slot="button" data-variant={variant} className={classes} {...props}>
        {children}
      </Slot.Root>
    );
  }

  return (
    <button
      data-slot="button"
      data-variant={variant}
      // A button inside a form submits it only when it says so.
      type={type ?? "button"}
      className={classes}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...props}
    >
      {loading ? (
        <>
          <span className="inline-flex items-center gap-2 opacity-0">{children}</span>
          <LoaderCircleIcon aria-hidden className="absolute animate-spin" />
        </>
      ) : (
        children
      )}
    </button>
  );
}

export { Button, buttonVariants };
export type { ButtonProps };
