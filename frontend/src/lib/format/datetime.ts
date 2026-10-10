/**
 * Dates and times (DESIGN.md §10). An instant is shown and entered in the `timezone` of
 * `/config`, whatever the device is set to, in the fixed locale en-GB: `14 Oct 2026, 18:00`.
 * A string that is no date is returned as it came, so a screen shows what the API sent
 * instead of failing.
 */

const LOCALE = "en-GB";
const DAY_MS = 86_400_000;

type Wall = { year: number; month: number; day: number; hour: number; minute: number };

/**
 * The month as DESIGN.md §10 writes it: three letters, `14 Oct 2026`. They are written
 * out here because the browser's own short names differ between versions ("Sep" in one,
 * "Sept" in the next), and a date must read the same on every device.
 */
const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
] as const;

const wallFormatters = new Map<string, Intl.DateTimeFormat>();

/** The date and time a clock on the wall shows at `instant` in `timeZone`. */
function wallTime(instant: Date, timeZone: string): Wall {
  let formatter = wallFormatters.get(timeZone);
  if (formatter === undefined) {
    formatter = new Intl.DateTimeFormat(LOCALE, {
      timeZone,
      year: "numeric",
      month: "numeric",
      day: "numeric",
      hour: "numeric",
      minute: "numeric",
      hourCycle: "h23",
    });
    wallFormatters.set(timeZone, formatter);
  }
  const wall: Wall = { year: 0, month: 0, day: 0, hour: 0, minute: 0 };
  for (const part of formatter.formatToParts(instant)) {
    if (part.type in wall) wall[part.type as keyof Wall] = Number.parseInt(part.value, 10);
  }
  return wall;
}

/** The name of a month of the calendar, 1 for January. */
function monthName(month: number): string {
  return MONTHS[month - 1] ?? String(month);
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

function parse(iso: string): Date | null {
  const instant = new Date(iso);
  return Number.isNaN(instant.getTime()) ? null : instant;
}

/** `14 Oct 2026` */
export function formatDate(iso: string, timeZone: string): string {
  const instant = parse(iso);
  if (instant === null) return iso;
  const wall = wallTime(instant, timeZone);
  return `${wall.day} ${monthName(wall.month)} ${wall.year}`;
}

/** `18:00` */
export function formatTime(iso: string, timeZone: string): string {
  const instant = parse(iso);
  if (instant === null) return iso;
  const wall = wallTime(instant, timeZone);
  return `${pad(wall.hour)}:${pad(wall.minute)}`;
}

/** `14 Oct 2026, 18:00` */
export function formatDateTime(iso: string, timeZone: string): string {
  if (parse(iso) === null) return iso;
  return `${formatDate(iso, timeZone)}, ${formatTime(iso, timeZone)}`;
}

/** Whole calendar days from `from` to `to`, counted on the calendar of `timeZone`. */
function calendarDaysBetween(from: Date, to: Date, timeZone: string): number {
  const a = wallTime(from, timeZone);
  const b = wallTime(to, timeZone);
  const start = Date.UTC(a.year, a.month - 1, a.day);
  const end = Date.UTC(b.year, b.month - 1, b.day);
  return Math.round((end - start) / DAY_MS);
}

export type DeadlineContext = {
  /** The current time in epoch milliseconds (`useNow`). Null where it is not known: the date is shown. */
  now: number | null;
  timeZone: string;
  /** `trading_open` of the market. Whether trading is possible is never read from the clock. */
  tradingOpen: boolean;
};

/**
 * A market's `close_at` in a list. Relative when it is under seven days away ("Closes
 * today, 18:00", "Closes in 3 days"), otherwise the date ("Closes 14 Oct 2026"). When
 * trading is not open, the date alone.
 */
export function formatDeadline(closeAt: string, context: DeadlineContext): string {
  const { now, timeZone, tradingOpen } = context;
  const close = parse(closeAt);
  if (close === null) return closeAt;
  const date = formatDate(closeAt, timeZone);
  if (!tradingOpen) return date;
  if (now === null) return `Closes ${date}`;

  const days = calendarDaysBetween(new Date(now), close, timeZone);
  if (days === 0) return `Closes today, ${formatTime(closeAt, timeZone)}`;
  if (days === 1) return "Closes in 1 day";
  if (days > 1 && days < 7) return `Closes in ${days} days`;
  return `Closes ${date}`;
}

/* ---- Entering a time (the propose form, the admin dialogs) ------------------------ */

const LOCAL_INPUT = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/;

/** How far the wall clock of `timeZone` is ahead of UTC at `utcMs`, in minutes. */
function offsetMinutes(utcMs: number, timeZone: string): number {
  const wall = wallTime(new Date(utcMs), timeZone);
  const asUtc = Date.UTC(wall.year, wall.month - 1, wall.day, wall.hour, wall.minute);
  // A wall clock shows whole minutes; the instant may carry seconds.
  return Math.round((asUtc - Math.floor(utcMs / 60_000) * 60_000) / 60_000);
}

/**
 * The value of a `datetime-local` input for an instant, as the wall time of `timeZone`:
 * `2026-10-14T18:00`. Empty for a string that is no date.
 */
export function toDateTimeInput(iso: string, timeZone: string): string {
  const instant = parse(iso);
  if (instant === null) return "";
  const wall = wallTime(instant, timeZone);
  const year = String(wall.year).padStart(4, "0");
  return `${year}-${pad(wall.month)}-${pad(wall.day)}T${pad(wall.hour)}:${pad(wall.minute)}`;
}

/**
 * What a `datetime-local` input holds, read as a wall time of `timeZone` and written as
 * ISO 8601 with that zone's offset: `2026-10-14T18:00` in Europe/Zurich is
 * `2026-10-14T18:00:00+02:00`. Null while the field is empty or incomplete.
 */
export function fromDateTimeInput(local: string, timeZone: string): string | null {
  const match = LOCAL_INPUT.exec(local);
  if (match === null) return null;
  const [year, month, day, hour, minute] = match
    .slice(1, 6)
    .map((part) => Number.parseInt(part, 10)) as [number, number, number, number, number];

  // The offset depends on the instant, and the instant on the offset: guess, then correct
  // once, which settles it on both sides of a clock change.
  const asUtc = Date.UTC(year, month - 1, day, hour, minute);
  let offset = offsetMinutes(asUtc, timeZone);
  offset = offsetMinutes(asUtc - offset * 60_000, timeZone);

  const sign = offset < 0 ? "-" : "+";
  const absolute = Math.abs(offset);
  const zone = `${sign}${pad(Math.floor(absolute / 60))}:${pad(absolute % 60)}`;
  return `${match[1]}-${match[2]}-${match[3]}T${match[4]}:${match[5]}:00${zone}`;
}
