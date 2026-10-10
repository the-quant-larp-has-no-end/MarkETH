"use client";

import { XIcon } from "lucide-react";
import type { ReactNode } from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Drawer,
  DrawerClose,
  DrawerContent,
  DrawerDescription,
  DrawerHeader,
  DrawerTitle,
} from "@/components/ui/drawer";
import { WIDE_QUERY } from "@/lib/design";
import { useMediaQuery } from "@/lib/hooks";
import { cn } from "@/lib/utils";

export type ResponsiveSheetProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Names the sheet. It is its heading, and what a screen reader announces on opening. */
  title: string;
  /** Keep the title for screen readers only, when the content says what the sheet is. */
  hideTitle?: boolean;
  /** One line under the title. */
  description?: ReactNode;
  /**
   * False while the sheet must stay: no drag, no tap on the scrim, no Escape and no close
   * button will close it.
   */
  dismissible?: boolean;
  /** `SheetBody`, then optionally `SheetFooter`. */
  children: ReactNode;
  className?: string;
};

/**
 * The bottom sheet that becomes a dialog (DESIGN.md §12). Below 768 px it slides up from
 * the bottom and closes by drag, by tapping the scrim or with the close button; from
 * 768 px it is a centred dialog, 420 px wide. Either way it is `--card` on `--scrim`, at
 * most 90% of the viewport tall, traps focus and returns it to the opener.
 *
 *     <ResponsiveSheet open={open} onOpenChange={setOpen} title="Redeem code">
 *       <SheetBody>…fields…</SheetBody>
 *       <SheetFooter><Button width="full">Redeem</Button></SheetFooter>
 *     </ResponsiveSheet>
 *
 * The content scrolls inside `SheetBody` while `SheetFooter` stays pinned above the safe
 * area. Crossing 768 px while it is open (turning a phone) mounts the content afresh, so
 * state that must survive lives above the sheet.
 */
export function ResponsiveSheet({
  open,
  onOpenChange,
  title,
  hideTitle = false,
  description,
  dismissible = true,
  children,
  className,
}: ResponsiveSheetProps) {
  const wide = useMediaQuery(WIDE_QUERY);
  const titleClasses = hideTitle ? "sr-only" : undefined;
  const headerClasses = hideTitle && description === undefined ? "sr-only" : undefined;
  // Without a description, say so: otherwise the dialog points at a text that is not there.
  const undescribed = description === undefined ? { "aria-describedby": undefined } : {};

  if (wide) {
    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent
          showCloseButton={dismissible}
          {...undescribed}
          onInteractOutside={dismissible ? undefined : (event) => event.preventDefault()}
          onEscapeKeyDown={dismissible ? undefined : (event) => event.preventDefault()}
          className={cn("gap-0 p-0", className)}
        >
          <DialogHeader className={cn("px-4 pt-4", headerClasses)}>
            <DialogTitle className={titleClasses}>{title}</DialogTitle>
            {description !== undefined && <DialogDescription>{description}</DialogDescription>}
          </DialogHeader>
          {children}
        </DialogContent>
      </Dialog>
    );
  }

  return (
    <Drawer open={open} onOpenChange={onOpenChange} dismissible={dismissible}>
      <DrawerContent {...undescribed} className={className}>
        <DrawerHeader className={headerClasses}>
          <DrawerTitle className={titleClasses}>{title}</DrawerTitle>
          {description !== undefined && <DrawerDescription>{description}</DrawerDescription>}
        </DrawerHeader>
        {dismissible && (
          <DrawerClose
            aria-label="Close"
            className="absolute top-1 right-1 inline-flex size-11 items-center justify-center rounded-md text-muted-foreground transition-colors duration-(--duration-fast) hover:bg-raised hover:text-foreground active:bg-raised"
          >
            <XIcon aria-hidden className="size-5" />
          </DrawerClose>
        )}
        {children}
      </DrawerContent>
    </Drawer>
  );
}

/** The part of a sheet that scrolls: 16 px padding, 16 px between its blocks. */
export function SheetBody({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      data-slot="sheet-body"
      className={cn("flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4", className)}
    >
      {children}
    </div>
  );
}

/**
 * The part of a sheet that stays: the action button, pinned at the bottom above the safe
 * area, with an inline error or a notice above it.
 */
export function SheetFooter({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      data-slot="sheet-footer"
      className={cn(
        "flex shrink-0 flex-col gap-3 px-4 pt-2 pb-[max(1rem,env(safe-area-inset-bottom))]",
        className,
      )}
    >
      {children}
    </div>
  );
}
