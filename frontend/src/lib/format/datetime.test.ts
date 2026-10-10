import { describe, expect, test } from "vitest";

import {
  formatDate,
  formatDateTime,
  formatDay,
  formatDeadline,
  formatTime,
  fromDateTimeInput,
  toDateTimeInput,
} from "./datetime";

const ZURICH = "Europe/Zurich";

describe("an instant is shown in the timezone of /config", () => {
  test("as 14 Oct 2026, 18:00", () => {
    // 16:00 UTC is 18:00 in Zurich while summer time lasts.
    expect(formatDateTime("2026-10-14T16:00:00Z", ZURICH)).toBe("14 Oct 2026, 18:00");
    expect(formatDate("2026-10-14T16:00:00Z", ZURICH)).toBe("14 Oct 2026");
    expect(formatTime("2026-10-14T16:00:00Z", ZURICH)).toBe("18:00");
  });

  test("whatever offset the API wrote it with", () => {
    expect(formatDateTime("2026-10-14T18:00:00+02:00", ZURICH)).toBe("14 Oct 2026, 18:00");
    expect(formatDateTime("2026-10-14T12:00:00-04:00", ZURICH)).toBe("14 Oct 2026, 18:00");
  });

  test("in winter the offset is one hour, and midnight is 00:00 of the next day", () => {
    expect(formatDateTime("2026-12-01T16:00:00Z", ZURICH)).toBe("1 Dec 2026, 17:00");
    expect(formatDateTime("2026-12-31T23:00:00Z", ZURICH)).toBe("1 Jan 2027, 00:00");
  });

  test("in another timezone the same instant reads differently", () => {
    expect(formatDateTime("2026-10-14T16:00:00Z", "UTC")).toBe("14 Oct 2026, 16:00");
    expect(formatDateTime("2026-10-14T16:00:00Z", "Asia/Tokyo")).toBe("15 Oct 2026, 01:00");
  });

  test("a string that is no date is shown as it came", () => {
    expect(formatDateTime("soon", ZURICH)).toBe("soon");
    expect(formatDate("", ZURICH)).toBe("");
  });

  test("every month is its three letters, whatever the device's own short names are", () => {
    const months = Array.from({ length: 12 }, (_, index) =>
      formatDate(`2026-${String(index + 1).padStart(2, "0")}-15T12:00:00Z`, ZURICH),
    );

    expect(months).toEqual([
      "15 Jan 2026",
      "15 Feb 2026",
      "15 Mar 2026",
      "15 Apr 2026",
      "15 May 2026",
      "15 Jun 2026",
      "15 Jul 2026",
      "15 Aug 2026",
      "15 Sep 2026",
      "15 Oct 2026",
      "15 Nov 2026",
      "15 Dec 2026",
    ]);
    expect(formatDateTime("2026-09-30T10:30:00Z", ZURICH)).toBe("30 Sep 2026, 12:30");
  });
});

describe("a day is shown as sent, without conversion", () => {
  test("1 Nov 2026", () => {
    expect(formatDay("2026-11-01")).toBe("1 Nov 2026");
    expect(formatDay("2027-01-31")).toBe("31 Jan 2027");
  });

  test("September is Sep here too", () => {
    expect(formatDay("2026-09-01")).toBe("1 Sep 2026");
  });

  test("anything else is shown as it came", () => {
    expect(formatDay("next month")).toBe("next month");
    expect(formatDay("2026-13-01")).toBe("2026-13-01");
  });
});

describe("a deadline in a list", () => {
  const at = (iso: string) => new Date(iso).getTime();
  const open = (now: string) => ({ now: at(now), timeZone: ZURICH, tradingOpen: true });
  const closeAt = "2026-10-14T16:00:00Z"; // 18:00 in Zurich

  test("is relative when it is under seven days away", () => {
    expect(formatDeadline(closeAt, open("2026-10-14T06:00:00Z"))).toBe("Closes today, 18:00");
    expect(formatDeadline(closeAt, open("2026-10-13T21:00:00Z"))).toBe("Closes in 1 day");
    expect(formatDeadline(closeAt, open("2026-10-11T10:00:00Z"))).toBe("Closes in 3 days");
    expect(formatDeadline(closeAt, open("2026-10-08T10:00:00Z"))).toBe("Closes in 6 days");
  });

  test("is the date from seven days on", () => {
    expect(formatDeadline(closeAt, open("2026-10-07T10:00:00Z"))).toBe("Closes 14 Oct 2026");
    expect(formatDeadline(closeAt, open("2026-08-01T10:00:00Z"))).toBe("Closes 14 Oct 2026");
  });

  test("counts days on the calendar of the configured timezone, not the device's", () => {
    // 22:30 UTC on the 13th is already the 14th in Zurich: the market closes today.
    expect(formatDeadline(closeAt, open("2026-10-13T22:30:00Z"))).toBe("Closes today, 18:00");
  });

  test("is the date alone when trading is not open, whatever the clock says", () => {
    const closed = { now: at("2026-10-14T06:00:00Z"), timeZone: ZURICH, tradingOpen: false };

    expect(formatDeadline(closeAt, closed)).toBe("14 Oct 2026");
  });

  test("is the date where no clock is known", () => {
    expect(formatDeadline(closeAt, { now: null, timeZone: ZURICH, tradingOpen: true })).toBe(
      "Closes 14 Oct 2026",
    );
  });
});

describe("a time the user enters", () => {
  test("is read as a wall time of the configured timezone and sent with its offset", () => {
    expect(fromDateTimeInput("2026-10-14T18:00", ZURICH)).toBe("2026-10-14T18:00:00+02:00");
    expect(fromDateTimeInput("2026-12-01T17:00", ZURICH)).toBe("2026-12-01T17:00:00+01:00");
    expect(fromDateTimeInput("2026-10-14T18:00", "UTC")).toBe("2026-10-14T18:00:00+00:00");
    expect(fromDateTimeInput("2026-10-14T18:00", "America/New_York")).toBe(
      "2026-10-14T18:00:00-04:00",
    );
    expect(fromDateTimeInput("2026-10-14T18:00", "Asia/Kolkata")).toBe("2026-10-14T18:00:00+05:30");
  });

  test("keeps its offset right on both sides of a clock change", () => {
    // Zurich leaves summer time on 25 Oct 2026 at 03:00.
    expect(fromDateTimeInput("2026-10-25T01:30", ZURICH)).toBe("2026-10-25T01:30:00+02:00");
    expect(fromDateTimeInput("2026-10-25T04:00", ZURICH)).toBe("2026-10-25T04:00:00+01:00");
    // And enters it on 28 Mar 2027 at 02:00.
    expect(fromDateTimeInput("2027-03-28T01:30", ZURICH)).toBe("2027-03-28T01:30:00+01:00");
    expect(fromDateTimeInput("2027-03-28T04:00", ZURICH)).toBe("2027-03-28T04:00:00+02:00");
  });

  test("is nothing while the field is empty or incomplete", () => {
    expect(fromDateTimeInput("", ZURICH)).toBeNull();
    expect(fromDateTimeInput("2026-10-14", ZURICH)).toBeNull();
  });

  test("fills a field from an instant, as the wall time of the configured timezone", () => {
    expect(toDateTimeInput("2026-10-14T16:00:00Z", ZURICH)).toBe("2026-10-14T18:00");
    expect(toDateTimeInput("2026-12-31T23:30:00Z", ZURICH)).toBe("2027-01-01T00:30");
    expect(toDateTimeInput("nonsense", ZURICH)).toBe("");
  });

  test("what was entered is what is shown again", () => {
    const sent = fromDateTimeInput("2026-10-14T18:00", ZURICH)!;

    expect(toDateTimeInput(sent, ZURICH)).toBe("2026-10-14T18:00");
    expect(formatDateTime(sent, ZURICH)).toBe("14 Oct 2026, 18:00");
  });
});
