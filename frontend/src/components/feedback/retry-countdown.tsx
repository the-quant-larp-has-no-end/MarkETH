"use client";

import type { ApiError } from "@/lib/api/errors";
import { useRetryAfter } from "@/lib/hooks";
import { cn } from "@/lib/utils";

/**
 * The countdown after `RATE_LIMITED` (DESIGN.md §14): "Try again in 12 s", counting down
 * the `retry_after_seconds` the API named, and nothing once the time is over or for any
 * other error. Put it next to the API's message; disable the action itself with
 * `useRetryAfter(error) > 0`.
 */
export function RetryCountdown({
  error,
  className,
}: {
  error: ApiError | null | undefined;
  className?: string;
}) {
  const wait = useRetryAfter(error);
  if (wait === 0) return null;

  return (
    <p
      data-slot="retry-countdown"
      className={cn("type-caption text-muted-foreground tabular-nums", className)}
    >
      Try again in {wait} s
    </p>
  );
}
