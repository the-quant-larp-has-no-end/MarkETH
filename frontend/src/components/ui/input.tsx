import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * The look of every form field (DESIGN.md §12): 48 px tall, `--background` fill, a 1 px
 * `--border-strong` outline, `--radius-md`, 16 px text so that iOS does not zoom. On
 * focus the border becomes `--ring` and 2 px; with an error it becomes `--no`.
 *
 * A refused field that has the focus keeps the colour of the error: its border is `--no`
 * and 2 px. The focus is where a refusal finds a field: an amount is refused while it is
 * typed, and a refused form takes the focus to its first refused field.
 */
export const fieldClasses = cn(
  "w-full min-w-0 rounded-md border border-border-strong bg-background px-3 type-body text-foreground",
  "placeholder:text-muted-foreground",
  "transition-[border-color] duration-(--duration-fast)",
  "focus-visible:border-ring focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring",
  "aria-invalid:border-no",
  "aria-invalid:focus-visible:border-no aria-invalid:focus-visible:outline-no",
  "disabled:cursor-not-allowed disabled:border-subtle disabled:text-subtle disabled:placeholder:text-subtle",
);

function Input({ className, type, ...props }: React.ComponentProps<"input">) {
  return (
    <input
      type={type}
      data-slot="input"
      className={cn(fieldClasses, "h-12", className)}
      {...props}
    />
  );
}

export { Input };
