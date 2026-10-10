"use client";

import type { ApiError } from "@/lib/api/errors";

import { ErrorBanner } from "./banner";
import { InlineError } from "./inline-error";
import { RetryCountdown } from "./retry-countdown";

/**
 * What a form or a dialog shows above its button for an answer that names none of its
 * fields (DESIGN.md §14). An answer of the API is shown inline, as sent, with the
 * countdown after `RATE_LIMITED`. No answer at all, and an error of the server, is the
 * banner: nobody can say then whether the request was carried out.
 *
 * `message` is the `form` of `placeError`: null when the error sits on a field instead.
 */
export function SubmitError({
  error,
  message,
}: {
  error: ApiError | null;
  message: string | null | undefined;
}) {
  if (error === null || message === null || message === undefined) return null;

  return (
    <div data-slot="submit-error" className="flex flex-col gap-1">
      {error.outcomeUnknown ? <ErrorBanner error={error} /> : <InlineError>{message}</InlineError>}
      <RetryCountdown error={error} />
    </div>
  );
}
