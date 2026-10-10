"use client";

import { createContext, type ReactNode, useContext, useEffect, useRef } from "react";

import {
  CloseCross,
  closeCrossClasses,
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
  DrawerNested,
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
  /**
   * With `hideTitle`: what stands in the header row in place of the title, beside the
   * close cross. One line that says what the sheet is about, as the question does on the
   * trade sheet.
   */
  header?: ReactNode;
  /** One line under the title. */
  description?: ReactNode;
  /**
   * False while the sheet must stay, because a request it sent is out and it has to show
   * what became of it: no drag, no tap on the scrim and no Escape closes it, and the close
   * cross stays in its place, disabled.
   */
  dismissible?: boolean;
  /**
   * A dialog at every width, as the welcome dialog is. Without it the sheet is a bottom
   * sheet below 768 px.
   */
  dialog?: boolean;
  /** `SheetBody`, then optionally `SheetFooter`. */
  children: ReactNode;
  className?: string;
};

/**
 * What a sheet tells its content: the form of the surface it stands in. A sheet that opens
 * from inside a bottom sheet (a picker in the filter sheet) reads it and nests itself.
 */
const SheetSurface = createContext<"sheet" | "dialog" | null>(null);

/** A picker's list that is open in place: the next Escape or click outside is the list's. */
const OPEN_LIST = '[role="combobox"][aria-expanded="true"]';

/**
 * The bottom sheet that becomes a dialog (DESIGN.md §12). Below 768 px it slides up from
 * the bottom and closes by drag, by tapping the scrim, with Escape or with the close
 * cross; from 768 px, and with `dialog` at every width, it is a centred dialog, 420 px
 * wide. Either way it is `--card` on `--scrim`, at most 90% of the viewport tall, traps
 * focus and returns it to the opener.
 *
 *     <ResponsiveSheet open={open} onOpenChange={setOpen} title="Redeem code">
 *       <SheetBody>…fields…</SheetBody>
 *       <SheetFooter><Button width="full">Redeem</Button></SheetFooter>
 *     </ResponsiveSheet>
 *
 * Top to bottom it is a header row, the body and the footer. The header row holds the
 * title and, at its right, the close cross, which every sheet and every dialog has; the
 * title ends 48 px before the right edge. The row does not scroll, also when the title is
 * hidden, so nothing lies under the cross and nothing scrolls under it. The content
 * scrolls inside `SheetBody` while `SheetFooter` stays pinned above the safe area, under
 * its 1 px line. Crossing 768 px while it is open (turning a phone) mounts the content
 * afresh, so state that must survive lives above the sheet.
 *
 * Focus (DESIGN.md §15). The sheet is opened by state, not by a trigger of its own, so it
 * notes what had the focus when it opened and gives the focus back to that when it has
 * closed. The dialog takes the focus to its first control. The bottom sheet takes it to
 * itself: a field that got the focus would slide a keyboard up over a sheet nobody has
 * read yet, and with the focus left on the page behind, the Tab key would walk off there.
 *
 * A sheet inside a sheet (DESIGN.md §12, Picker). Below 768 px a sheet that is opened from
 * the content of a bottom sheet is nested in it: the sheet under it stays open and keeps
 * the page locked when the upper one closes. As a dialog, the sheet leaves Escape and a
 * click beside it to a picker whose list is open in it: the list closes and the dialog
 * stays.
 */
export function ResponsiveSheet({
  open,
  onOpenChange,
  title,
  hideTitle = false,
  header,
  description,
  dismissible = true,
  dialog = false,
  children,
  className,
}: ResponsiveSheetProps) {
  const wide = useMediaQuery(WIDE_QUERY);
  const around = useContext(SheetSurface);
  /** Whether a picker's list was open in the dialog when the pointer went down. */
  const listWasOpen = useRef(false);
  /** What had the focus when the sheet opened. */
  const opener = useRef<HTMLElement | null>(null);
  const isOpen = useRef(open);
  useEffect(() => {
    isOpen.current = open;
  }, [open]);

  // Noted when the pointer goes down, before anything else hears of it: by the time the
  // dialog asks what a touch beside it means, the list has closed already.
  useEffect(() => {
    if (!open) return;
    const note = () => {
      listWasOpen.current = document.querySelector(OPEN_LIST) !== null;
    };
    document.addEventListener("pointerdown", note, true);
    return () => document.removeEventListener("pointerdown", note, true);
  }, [open]);

  const rememberOpener = () => {
    // Turning a phone swaps the bottom sheet for the dialog: the opening is still the same.
    if (opener.current !== null) return;
    const focused = document.activeElement;
    opener.current = focused instanceof HTMLElement && focused !== document.body ? focused : null;
  };
  const returnFocus = (event: Event) => {
    // Still open: only the other form of the sheet took over. The focus stays in it.
    if (isOpen.current) return event.preventDefault();
    const from = opener.current;
    opener.current = null;
    if (from === null || !from.isConnected) return;
    event.preventDefault();
    from.focus();
  };

  // Without a description, say so: otherwise the dialog points at a text that is not there.
  const undescribed = description === undefined ? { "aria-describedby": undefined } : {};

  if (wide || dialog) {
    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent
          locked={!dismissible}
          {...undescribed}
          onOpenAutoFocus={rememberOpener}
          onCloseAutoFocus={returnFocus}
          onEscapeKeyDown={(event) => {
            if (event.target instanceof Element && event.target.matches(OPEN_LIST)) {
              event.preventDefault();
            }
          }}
          onInteractOutside={(event) => {
            if (listWasOpen.current) event.preventDefault();
            listWasOpen.current = false;
          }}
          className={cn("gap-0 p-0", sheetHeaderClasses, className)}
        >
          {hideTitle ? (
            <>
              <DialogTitle className="sr-only">{title}</DialogTitle>
              {description !== undefined && (
                <DialogDescription className="sr-only">{description}</DialogDescription>
              )}
              {/* As tall as the cross beside it: 4 px above its 44 px. */}
              <HeaderRow className="min-h-12">{header}</HeaderRow>
            </>
          ) : (
            <DialogHeader className="shrink-0 px-4 pt-4 pb-2">
              <DialogTitle>{title}</DialogTitle>
              {description !== undefined && <DialogDescription>{description}</DialogDescription>}
            </DialogHeader>
          )}
          <SheetSurface value="dialog">{children}</SheetSurface>
        </DialogContent>
      </Dialog>
    );
  }

  // Inside a bottom sheet this one is nested. Inside a dialog it is a sheet of its own.
  const Root = around === "sheet" ? DrawerNested : Drawer;

  return (
    <Root open={open} onOpenChange={onOpenChange} dismissible={dismissible}>
      <DrawerContent
        {...undescribed}
        onOpenAutoFocus={(event) => {
          rememberOpener();
          event.preventDefault();
          (event.currentTarget as HTMLElement | null)?.focus({ preventScroll: true });
        }}
        onCloseAutoFocus={returnFocus}
        className={cn(sheetHeaderClasses, className)}
      >
        {hideTitle ? (
          <>
            <DrawerTitle className="sr-only">{title}</DrawerTitle>
            {description !== undefined && (
              <DrawerDescription className="sr-only">{description}</DrawerDescription>
            )}
            {/* The handle above it is 12 px of the 48 px the cross takes. */}
            <HeaderRow className="min-h-9">{header}</HeaderRow>
          </>
        ) : (
          <DrawerHeader className="shrink-0 pb-2">
            <DrawerTitle>{title}</DrawerTitle>
            {description !== undefined && <DrawerDescription>{description}</DrawerDescription>}
          </DrawerHeader>
        )}
        <SheetSurface value="sheet">{children}</SheetSurface>
        {/* After the content, as in the dialog: the cross is the last stop of the Tab key. */}
        <DrawerClose aria-label="Close" disabled={!dismissible} className={closeCrossClasses}>
          <CloseCross />
        </DrawerClose>
      </DrawerContent>
    </Root>
  );
}

/**
 * The header row of a sheet whose title is hidden. It keeps the height of the close cross
 * also when it holds nothing, and what it holds ends 48 px before the right edge.
 */
function HeaderRow({ className, children }: { className: string; children: ReactNode }) {
  return (
    <div
      data-slot="sheet-header"
      className={cn("flex shrink-0 items-center pr-12 pl-4", className)}
    >
      {children !== undefined && children !== null && (
        <div className="min-w-0 flex-1">{children}</div>
      )}
    </div>
  );
}

/**
 * For the surface around a `SheetBody` that stands under a header row: the header keeps
 * 8 px of its own below it (`pb-2`), and the body starts that much higher. The distance
 * between header and content stays 16 px, and what scrolls is cut below the header
 * instead of at its foot. `ResponsiveSheet` sets it itself; a surface that builds its own
 * frame from the dialog primitives (a confirmation dialog) takes it too.
 */
export const sheetHeaderClasses = "[--sheet-body-top:0.5rem]";

/**
 * The part of a sheet that scrolls: 16 px padding, 16 px between its blocks. Each block
 * keeps its height: on a short phone the body scrolls, and nothing in it is squeezed.
 */
export function SheetBody({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      data-slot="sheet-body"
      className={cn(
        "flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-4 pt-[var(--sheet-body-top,1rem)] pb-4 *:shrink-0",
        className,
      )}
    >
      {children}
    </div>
  );
}

/**
 * The part of a sheet that stays: the action button, pinned at the bottom above the safe
 * area, with an inline error or a notice above it. The 1 px `--border` line on top of it
 * shows where the part that scrolls ends (DESIGN.md §12).
 */
export function SheetFooter({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      data-slot="sheet-footer"
      className={cn(
        "flex shrink-0 flex-col gap-3 border-t px-4 pt-3 pb-[max(1rem,env(safe-area-inset-bottom))]",
        className,
      )}
    >
      {children}
    </div>
  );
}
