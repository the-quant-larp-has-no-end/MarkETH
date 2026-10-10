"use client";

import * as React from "react";
import { RadioGroup as RadioGroupPrimitive } from "radix-ui";

import { cn } from "@/lib/utils";

/**
 * One choice of a few, each with room for a line of explanation: the liquidity tiers of
 * the propose form are radio cards built on this (DESIGN.md §13). The mark is 20 px; the
 * tap target is the card or the row around it.
 */
function RadioGroup({
  className,
  ...props
}: React.ComponentProps<typeof RadioGroupPrimitive.Root>) {
  return (
    <RadioGroupPrimitive.Root
      data-slot="radio-group"
      className={cn("grid gap-3", className)}
      {...props}
    />
  );
}

function RadioGroupItem({
  className,
  ...props
}: React.ComponentProps<typeof RadioGroupPrimitive.Item>) {
  return (
    <RadioGroupPrimitive.Item
      data-slot="radio-group-item"
      className={cn(
        "flex aspect-square size-5 shrink-0 items-center justify-center rounded-full border border-border-strong text-foreground",
        "transition-[border-color] duration-(--duration-fast)",
        "data-[state=checked]:border-foreground aria-invalid:border-no",
        "disabled:cursor-not-allowed disabled:border-subtle disabled:text-subtle",
        className,
      )}
      {...props}
    >
      <RadioGroupPrimitive.Indicator
        data-slot="radio-group-indicator"
        className="size-2.5 rounded-full bg-current"
      />
    </RadioGroupPrimitive.Item>
  );
}

export { RadioGroup, RadioGroupItem };
