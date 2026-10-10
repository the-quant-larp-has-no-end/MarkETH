import { CircleAlertIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * An error next to what it concerns (DESIGN.md §12): under a field, or above the action
 * button of a sheet or a form. The text is the API's `message`; the icon is there because
 * colour never carries meaning alone. It is announced when it appears.
 *
 * Under a field, pass the `id` that the field names in `aria-describedby`; the form
 * fields of `@/components/form` do that themselves.
 */
export function InlineError({
  id,
  children,
  className,
}: {
  id?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <p
      id={id}
      role="alert"
      data-slot="inline-error"
      className={cn("flex items-start gap-2 type-body-sm text-no-text", className)}
    >
      <CircleAlertIcon aria-hidden className="size-5 shrink-0" />
      <span className="min-w-0 wrap-break-word">{children}</span>
    </p>
  );
}
