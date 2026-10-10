import { CircleAlertIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * An error next to what it concerns (DESIGN.md §12): under a field, or above the action
 * button of a sheet or a form. The text is the API's `message`; the icon is there because
 * colour never carries meaning alone. It is announced when it appears.
 *
 * `tone="notice"` is for a message of the API that reports no fault: the price moved, and
 * the quote on screen is another one now (DESIGN.md §13, `QUOTE_STALE`). It stands in the
 * same place, with the same icon, and is announced the same; red stays for errors.
 *
 * Under a field, pass the `id` that the field names in `aria-describedby`; the form
 * fields of `@/components/form` do that themselves.
 */
export function InlineError({
  id,
  tone = "error",
  children,
  className,
}: {
  id?: string;
  /** `error` is `--no-text`; `notice` is `--foreground`. */
  tone?: "error" | "notice";
  children: ReactNode;
  className?: string;
}) {
  return (
    <p
      id={id}
      role="alert"
      data-slot="inline-error"
      data-tone={tone}
      className={cn(
        "flex items-start gap-2 type-body-sm",
        tone === "notice" ? "text-foreground" : "text-no-text",
        className,
      )}
    >
      <CircleAlertIcon aria-hidden className="size-5 shrink-0" />
      <span className="min-w-0 wrap-break-word">{children}</span>
    </p>
  );
}
