"use client";

import * as React from "react";
import { XIcon } from "lucide-react";
import { Dialog as DialogPrimitive } from "radix-ui";

import { cn } from "@/lib/utils";

/**
 * The dialog of DESIGN.md §12: `--card`, `--radius-xl`, `--scrim` behind, 420 px wide and
 * never wider than the viewport less 16 px on each side, at most 90% of the viewport
 * tall. Together with the bottom sheet it carries the only shadow of the system. It
 * enters in `--duration-sheet`; with reduced motion it fades without moving.
 *
 * Every dialog has the close cross at its top right. Screens open a dialog through
 * `ResponsiveSheet` (with `dialog` for one that is a dialog at every width), which adds
 * the header row, the focus handling and the pinned footer.
 */

function Dialog({ ...props }: React.ComponentProps<typeof DialogPrimitive.Root>) {
  return <DialogPrimitive.Root data-slot="dialog" {...props} />;
}

function DialogTrigger({ ...props }: React.ComponentProps<typeof DialogPrimitive.Trigger>) {
  return <DialogPrimitive.Trigger data-slot="dialog-trigger" {...props} />;
}

function DialogPortal({ ...props }: React.ComponentProps<typeof DialogPrimitive.Portal>) {
  return <DialogPrimitive.Portal data-slot="dialog-portal" {...props} />;
}

function DialogClose({ ...props }: React.ComponentProps<typeof DialogPrimitive.Close>) {
  return <DialogPrimitive.Close data-slot="dialog-close" {...props} />;
}

export const overlayClasses = cn(
  "fixed inset-0 z-50 bg-scrim duration-(--duration-sheet)",
  "data-[state=open]:animate-in data-[state=open]:ease-(--ease-out) data-[state=open]:fade-in-0",
  "data-[state=closed]:animate-out data-[state=closed]:ease-(--ease-in) data-[state=closed]:fade-out-0",
);

export const dialogSurfaceClasses = cn(
  "fixed top-1/2 left-1/2 z-50 flex max-h-[90dvh] w-[calc(100%-2rem)] max-w-[26.25rem] -translate-x-1/2 -translate-y-1/2 flex-col",
  "rounded-xl border bg-card text-card-foreground shadow-lg shadow-ink/40 outline-none",
  "duration-(--duration-sheet)",
  "data-[state=open]:animate-in data-[state=open]:ease-(--ease-out) data-[state=open]:fade-in-0 motion-safe:data-[state=open]:zoom-in-95",
  "data-[state=closed]:animate-out data-[state=closed]:ease-(--ease-in) data-[state=closed]:fade-out-0 motion-safe:data-[state=closed]:zoom-out-95",
);

function DialogOverlay({
  className,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Overlay>) {
  return (
    <DialogPrimitive.Overlay
      data-slot="dialog-overlay"
      className={cn(overlayClasses, className)}
      {...props}
    />
  );
}

/**
 * The close cross of every sheet and every dialog (DESIGN.md §12): a 24 px cross in
 * `--foreground` inside a 44 x 44 px target, 4 px from the top and from the right edge.
 * It is never taken away: while its surface cannot be closed it stays in its place,
 * disabled, in `--subtle`.
 */
export const closeCrossClasses = cn(
  "absolute top-1 right-1 inline-flex size-11 items-center justify-center rounded-md text-foreground",
  "transition-colors duration-(--duration-fast) hover:bg-raised active:bg-raised",
  "disabled:pointer-events-none disabled:text-subtle",
);

/** What the close cross draws. Its button names it: `aria-label="Close"`. */
export function CloseCross() {
  return <XIcon aria-hidden className="size-6" />;
}

/**
 * What a header row gives the close cross (DESIGN.md §12): its title ends 48 px before the
 * right edge of the surface. The header stands 16 px from that edge, by the padding of
 * its surface or by its own, and keeps the other 32 px itself.
 */
export const besideCloseCrossClasses = "mr-8";

function DialogContent({
  className,
  children,
  locked = false,
  onEscapeKeyDown,
  onInteractOutside,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Content> & {
  /**
   * The dialog cannot be closed right now: a request it sent is out, and it has to show
   * what became of it. No tap beside it and no Escape closes it, and the close cross
   * stays in its place, disabled.
   */
  locked?: boolean;
}) {
  return (
    <DialogPortal>
      <DialogOverlay />
      <DialogPrimitive.Content
        data-slot="dialog-content"
        className={cn(dialogSurfaceClasses, "gap-4 p-4", className)}
        {...props}
        onEscapeKeyDown={(event) => {
          if (locked) event.preventDefault();
          onEscapeKeyDown?.(event);
        }}
        onInteractOutside={(event) => {
          if (locked) event.preventDefault();
          onInteractOutside?.(event);
        }}
      >
        {children}
        {/* After the content: the dialog takes the focus to its first control, not to the cross. */}
        <DialogPrimitive.Close
          data-slot="dialog-close"
          aria-label="Close"
          disabled={locked}
          className={closeCrossClasses}
        >
          <CloseCross />
        </DialogPrimitive.Close>
      </DialogPrimitive.Content>
    </DialogPortal>
  );
}

function DialogHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="dialog-header"
      className={cn("flex flex-col gap-2", besideCloseCrossClasses, className)}
      {...props}
    />
  );
}

function DialogFooter({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="dialog-footer"
      className={cn("flex flex-col-reverse gap-2 md:flex-row md:justify-end", className)}
      {...props}
    />
  );
}

function DialogTitle({ className, ...props }: React.ComponentProps<typeof DialogPrimitive.Title>) {
  return (
    <DialogPrimitive.Title
      data-slot="dialog-title"
      className={cn("type-h2", className)}
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
      data-slot="dialog-description"
      className={cn("type-body-sm text-muted-foreground", className)}
      {...props}
    />
  );
}

export {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogOverlay,
  DialogPortal,
  DialogTitle,
  DialogTrigger,
};
