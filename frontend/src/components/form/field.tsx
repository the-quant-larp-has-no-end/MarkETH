"use client";

import { type ReactNode, useId } from "react";

import { InlineError } from "@/components/feedback";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

/** What a field hands to its control so that label, help and error are wired to it. */
export type ControlProps = {
  id: string;
  "aria-describedby": string | undefined;
  "aria-invalid": true | undefined;
};

export type FieldProps = {
  label: string;
  /** Help text below the field, in `caption`: what may be typed, a limit from `/config`. */
  help?: ReactNode;
  /**
   * The API's message for this field (`placeError`). It turns the border `--no` and is
   * read out when it appears.
   */
  error?: string | null;
  /** Shown at the right under the field, e.g. `37 / 200`. */
  counter?: ReactNode;
  className?: string;
};

/**
 * The frame of a form field (DESIGN.md §12): the label above (`caption`,
 * `--muted-foreground`), 8 px, the control, then the error or the help text below. The
 * control gets its ids from here: `aria-describedby` names the help and the error, and
 * `aria-invalid` is set while there is an error.
 *
 * `TextField`, `TextAreaField`, `SelectField`, `DateTimeField` and `CodeInput` are built
 * on it; use it directly for a control of your own.
 */
export function Field({
  label,
  help,
  error,
  counter,
  className,
  children,
}: FieldProps & { children: (control: ControlProps) => ReactNode }) {
  const id = useId();
  const helpId = `${id}-help`;
  const errorId = `${id}-error`;
  const hasError = typeof error === "string" && error !== "";
  const describedBy =
    [hasError ? errorId : null, help !== undefined ? helpId : null].filter(Boolean).join(" ") ||
    undefined;

  return (
    <div data-slot="field" className={cn("flex flex-col gap-2", className)}>
      <Label htmlFor={id}>{label}</Label>
      {children({
        id,
        "aria-describedby": describedBy,
        "aria-invalid": hasError ? true : undefined,
      })}
      {hasError && <InlineError id={errorId}>{error}</InlineError>}
      {(help !== undefined || counter !== undefined) && (
        <div className="flex items-start justify-between gap-3 type-caption text-muted-foreground">
          <span id={helpId}>{help}</span>
          {counter !== undefined && <span className="shrink-0 tabular-nums">{counter}</span>}
        </div>
      )}
    </div>
  );
}
