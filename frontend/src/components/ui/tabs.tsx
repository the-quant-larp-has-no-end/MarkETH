"use client";

import * as React from "react";
import { Tabs as TabsPrimitive } from "radix-ui";

import { keepInSight } from "@/lib/keep-in-sight";
import { cn } from "@/lib/utils";

/**
 * Tabs switch between the views of one screen (DESIGN.md §12): a row 48 px tall with
 * `label` text. The active tab is `--foreground` with a 2 px `--foreground` line under
 * it; the others are `--muted-foreground`. There is no coloured indicator. A row that is
 * wider than the screen scrolls sideways and keeps the active tab in sight.
 */

/**
 * The classes of one tab, shared with tabs that are links (`LinkTabs`). The row of tabs
 * scrolls sideways when it has to, and so cuts off what stands outside it: the focus
 * outline of a tab is drawn inside the tab.
 */
export const tabClasses = cn(
  "relative inline-flex h-12 shrink-0 items-center justify-center px-4 type-label whitespace-nowrap text-muted-foreground",
  "transition-colors duration-(--duration-base) hover:text-foreground",
  "after:absolute after:inset-x-0 after:bottom-0 after:h-0.5 after:bg-foreground after:opacity-0 after:transition-opacity after:duration-(--duration-base)",
  "focus-visible:-outline-offset-2",
  "disabled:pointer-events-none disabled:text-subtle",
);

export const activeTabClasses = "text-foreground after:opacity-100";

export const tabListClasses = "flex h-12 w-full items-stretch overflow-x-auto border-b";

function Tabs({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.Root>) {
  return (
    <TabsPrimitive.Root
      data-slot="tabs"
      className={cn("flex flex-col gap-4", className)}
      {...props}
    />
  );
}

/**
 * Follows which tab of the row is the active one, and keeps it in sight: when the row
 * appears, and whenever another tab becomes active. Only the row moves, never the page.
 */
function followActiveTab(row: HTMLElement | null) {
  if (row === null) return;
  const look = () => keepInSight(row, row.querySelector('[data-state="active"]'));
  look();
  const tabs = new MutationObserver(look);
  tabs.observe(row, { attributes: true, attributeFilter: ["data-state"], subtree: true });
  return () => tabs.disconnect();
}

function TabsList({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.List>) {
  return (
    <TabsPrimitive.List
      ref={followActiveTab}
      data-slot="tabs-list"
      className={cn(tabListClasses, className)}
      {...props}
    />
  );
}

/**
 * One tab. The primitive focuses a tab itself when it is pressed, and a browser may take a
 * focus that a script gave for the keyboard's: the focus ring would then follow a click
 * or a tap. So a tab notes whether its focus came with a press (`data-by-pointer`) and
 * shows no ring then, until a key is pressed on it or the focus leaves it.
 */
function TabsTrigger({
  className,
  onMouseDown,
  onFocus,
  onKeyDown,
  onBlur,
  ...props
}: React.ComponentProps<typeof TabsPrimitive.Trigger>) {
  const pressing = React.useRef(false);
  const [byPointer, setByPointer] = React.useState(false);

  return (
    <TabsPrimitive.Trigger
      data-slot="tabs-trigger"
      data-by-pointer={byPointer ? "" : undefined}
      className={cn(
        tabClasses,
        "data-[state=active]:text-foreground data-[state=active]:after:opacity-100",
        "data-by-pointer:outline-none",
        className,
      )}
      {...props}
      onMouseDown={(event) => {
        // The primitive's own handler runs after this one and focuses the tab. A press
        // that focuses nothing (another mouse button) is forgotten when both are done.
        pressing.current = true;
        queueMicrotask(() => {
          pressing.current = false;
        });
        onMouseDown?.(event);
      }}
      onFocus={(event) => {
        setByPointer(pressing.current);
        onFocus?.(event);
      }}
      onKeyDown={(event) => {
        setByPointer(false);
        onKeyDown?.(event);
      }}
      onBlur={(event) => {
        setByPointer(false);
        onBlur?.(event);
      }}
    />
  );
}

/**
 * The view of the chosen tab. It takes the keyboard's focus after its tab, so that a view
 * without a control of its own (an empty list) can still be reached, and shows that focus
 * like every other stop: it keeps the ring of `:focus-visible`.
 */
function TabsContent({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.Content>) {
  return (
    <TabsPrimitive.Content
      data-slot="tabs-content"
      className={cn("flex-1 rounded-sm", className)}
      {...props}
    />
  );
}

export { Tabs, TabsContent, TabsList, TabsTrigger };
