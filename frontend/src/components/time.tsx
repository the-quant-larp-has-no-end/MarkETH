"use client";

import { useConfig } from "@/lib/api/session";
import { formatDate, formatDateTime } from "@/lib/format";

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
