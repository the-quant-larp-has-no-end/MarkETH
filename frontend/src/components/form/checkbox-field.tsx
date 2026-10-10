"use client";

import { type ReactNode, useId } from "react";

import { InlineError } from "@/components/feedback";
import { Checkbox } from "@/components/ui/checkbox";
import { cn } from "@/lib/utils";

export type CheckboxFieldProps = {
  /**
   * The text of the row: what a tick says. It names the checkbox. A link inside it is a
   * target of its own: it opens what it names and leaves the box as it is.
   */
  children: ReactNode;
  checked: boolean;
  onChange: (checked: boolean) => void;
  /** The field's name in the request, e.g. `policy_accepted`. */
  name?: string;
  /**
   * The API's message for this field (`placeError`). It stands below the row, turns the
   * box's border `--no` and is read out when it appears.
   */
  error?: string | null;
  disabled?: boolean;
  className?: string;
};

/**
 * A checkbox with its text (DESIGN.md §12): the 20 px box and the text form one row at
 * least 44 px tall, and a tap anywhere on the text ticks the box. An error stands below
 * the row, as under a field, and `revealFirstInvalid` finds the box by it.
 *
 *     <CheckboxField name="one_per_user" checked={one} onChange={setOne}>
 *       One code per person
 *     </CheckboxField>
 *
 * Ticking it posts nothing: it sets what a button then sends (DESIGN.md §14).
 */
export function CheckboxField({
  children,
  checked,
  onChange,
  name,
  error,
  disabled = false,
  className,
}: CheckboxFieldProps) {
  const id = useId();
  const errorId = `${id}-error`;
  const hasError = typeof error === "string" && error !== "";

  return (
    <div data-slot="checkbox-field" className={cn("flex flex-col gap-2", className)}>
      <label
        className={cn(
          "flex min-h-11 items-center gap-3 type-body",
          disabled ? "cursor-not-allowed text-subtle" : "cursor-pointer",
        )}
      >
        <Checkbox
          name={name}
          checked={checked}
          onCheckedChange={(next) => onChange(next === true)}
          disabled={disabled}
          aria-describedby={hasError ? errorId : undefined}
          aria-invalid={hasError ? true : undefined}
        />
        <span className="min-w-0 wrap-break-word">{children}</span>
      </label>
      {hasError && <InlineError id={errorId}>{error}</InlineError>}
    </div>
  );
}
