"use client";

import { useConfig } from "@/lib/api/session";
import { formatDeadline } from "@/lib/format";
import { useNow } from "@/lib/hooks";

/**
 * A market's `close_at` in a list (DESIGN.md §10): relative when it is under seven days
 * away ("Closes in 3 days", "Closes today, 18:00"), otherwise the date. When trading is
 * not open, the date alone. Whether trading is open comes from `trading_open`, never
 * from the clock.
 */
export function Deadline({
  closeAt,
  tradingOpen,
  className,
}: {
  closeAt: string;
  tradingOpen: boolean;
  className?: string;
}) {
  const { timezone } = useConfig();
  const now = useNow(60_000);

  return (
    <time dateTime={closeAt} data-slot="deadline" className={className}>
      {formatDeadline(closeAt, { now, timeZone: timezone, tradingOpen })}
    </time>
  );
}
