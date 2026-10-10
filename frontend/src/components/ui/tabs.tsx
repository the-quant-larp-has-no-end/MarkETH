"use client";

import * as React from "react";
import { Tabs as TabsPrimitive } from "radix-ui";

import { cn } from "@/lib/utils";

/**
 * Tabs switch between the views of one screen (DESIGN.md §12): a row 48 px tall with
 * `label` text. The active tab is `--foreground` with a 2 px `--foreground` line under
 * it; the others are `--muted-foreground`. There is no coloured indicator.
 */

/** The classes of one tab, shared with tabs that are links (`LinkTabs`). */
export const tabClasses = cn(
  "relative inline-flex h-12 shrink-0 items-center justify-center px-4 type-label whitespace-nowrap text-muted-foreground",
  "transition-colors duration-(--duration-base) hover:text-foreground",
  "after:absolute after:inset-x-0 after:bottom-0 after:h-0.5 after:bg-foreground after:opacity-0 after:transition-opacity after:duration-(--duration-base)",
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

function TabsList({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.List>) {
  return (
    <TabsPrimitive.List
      data-slot="tabs-list"
      className={cn(tabListClasses, className)}
      {...props}
    />
  );
}

function TabsTrigger({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.Trigger>) {
  return (
    <TabsPrimitive.Trigger
      data-slot="tabs-trigger"
      className={cn(
        tabClasses,
        "data-[state=active]:text-foreground data-[state=active]:after:opacity-100",
        className,
      )}
      {...props}
    />
  );
}

function TabsContent({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.Content>) {
  return (
    <TabsPrimitive.Content
      data-slot="tabs-content"
      className={cn("flex-1 outline-none", className)}
      {...props}
    />
  );
}

export { Tabs, TabsContent, TabsList, TabsTrigger };
