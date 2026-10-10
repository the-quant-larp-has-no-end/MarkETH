"use client";

import { useConfig } from "@/lib/api/session";
import { formatDate, formatDateTime, formatDay } from "@/lib/format";

/**
 * An instant the API sent, in the timezone of `/config` whatever the device is set to
 * (DESIGN.md §10): `14 Oct 2026, 18:00`, or with `dateOnly` `14 Oct 2026`.
 */
export function DateTime({
  value,
  dateOnly = false,
  className,
}: {
  value: string;
  dateOnly?: boolean;
  className?: string;
}) {
  const { timezone } = useConfig();

  return (
    <time dateTime={value} className={className}>
      {dateOnly ? formatDate(value, timezone) : formatDateTime(value, timezone)}
    </time>
  );
}

/**
 * A calendar day the API sent as a date (`next_monthly_grant_on`): the day as sent,
 * without conversion, `1 Nov 2026`.
 */
export function Day({ value, className }: { value: string; className?: string }) {
  return (
    <time dateTime={value} className={className}>
      {formatDay(value)}
    </time>
  );
}
