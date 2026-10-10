import { act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { config } from "@/test/fixtures";
import { renderHookWithProviders } from "@/test/render";

import { useAskAtClose } from "./close-time";

const NOW = new Date("2026-10-14T15:00:00Z");
/** One hour from now. */
const CLOSE_AT = "2026-10-14T16:00:00Z";
const HOUR = 60 * 60 * 1000;
const DAY = 24 * HOUR;
/** The longest wait a browser's timer holds: its delay is a signed 32-bit integer. */
const LONGEST_TIMER_MS = 2 ** 31 - 1;

/** The hook as a screen uses it, with `live_poll_fallback_seconds` of 10. */
function watch(closeTimes: readonly string[], pollSeconds = 10) {
  const ask = vi.fn();
  const view = renderHookWithProviders(
    ({ times }: { times: readonly string[] }) => useAskAtClose(times, ask),
    {
      initialProps: { times: closeTimes },
      config: config({ live_poll_fallback_seconds: pollSeconds }),
    },
  );
  return { ask, ...view };
}

const pass = (ms: number) => act(() => vi.advanceTimersByTime(ms));

beforeEach(() => {
  vi.useFakeTimers({ now: NOW });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useAskAtClose", () => {
  test("asks when the close time passes, and not before", () => {
    const { ask } = watch([CLOSE_AT]);

    pass(HOUR - 1);
    expect(ask).not.toHaveBeenCalled();

    pass(1);
    expect(ask).toHaveBeenCalledTimes(1);
  });

  test("sets one timer, not one per render", () => {
    const { ask, rerender } = watch([CLOSE_AT]);
    const set = vi.spyOn(window, "setTimeout");
    const clear = vi.spyOn(window, "clearTimeout");

    // The same close time arrives in another list, as every answer brings one.
    rerender({ times: [CLOSE_AT] });
    rerender({ times: [CLOSE_AT] });

    expect(set).not.toHaveBeenCalled();
    expect(clear).not.toHaveBeenCalled();
    pass(HOUR);
    expect(ask).toHaveBeenCalledTimes(1);
  });

  test("is for the earliest of the close times it is given", () => {
    const { ask } = watch(["2026-10-16T16:00:00Z", CLOSE_AT, "2026-10-15T16:00:00Z"]);

    pass(HOUR);

    expect(ask).toHaveBeenCalledTimes(1);
  });

  test("orders close times as the instants they are, whatever offset they are written with", () => {
    // 17:30 in Zurich is 15:30 UTC: the earlier of the two, though it reads later.
    const { ask } = watch([CLOSE_AT, "2026-10-14T17:30:00+02:00"]);

    pass(30 * 60 * 1000);

    expect(ask).toHaveBeenCalledTimes(1);
  });

  test("is set again when the close time is another one: after the answer, the earliest that is left", () => {
    const { ask, rerender } = watch([CLOSE_AT, "2026-10-14T18:00:00Z"]);
    pass(HOUR);
    expect(ask).toHaveBeenCalledTimes(1);

    // The answer: the first market is closed, the second is open for trading.
    rerender({ times: ["2026-10-14T18:00:00Z"] });

    pass(2 * HOUR - 1);
    expect(ask).toHaveBeenCalledTimes(1);
    pass(1);
    expect(ask).toHaveBeenCalledTimes(2);
  });

  test("a close time that moved is waited for afresh", () => {
    const { ask, rerender } = watch([CLOSE_AT]);

    rerender({ times: ["2026-10-14T15:10:00Z"] });
    pass(10 * 60 * 1000);
    expect(ask).toHaveBeenCalledTimes(1);

    // The timer of the time that was dropped never fires.
    rerender({ times: [] });
    pass(DAY);
    expect(ask).toHaveBeenCalledTimes(1);
  });

  test("decides nothing: while the same market is still shown as open, it asks again every live_poll_fallback_seconds", () => {
    // The device's clock runs ahead of the server's, or the answer did not arrive.
    const { ask } = watch([CLOSE_AT], 10);
    pass(HOUR);
    expect(ask).toHaveBeenCalledTimes(1);

    pass(9_999);
    expect(ask).toHaveBeenCalledTimes(1);
    pass(1);
    expect(ask).toHaveBeenCalledTimes(2);
    pass(10_000);
    expect(ask).toHaveBeenCalledTimes(3);
  });

  test("the pace of asking again is the one of /config", () => {
    const { ask } = watch([CLOSE_AT], 3);
    pass(HOUR);

    pass(3_000);
    expect(ask).toHaveBeenCalledTimes(2);
    pass(6_000);
    expect(ask).toHaveBeenCalledTimes(4);
  });

  test("stops asking once no market is shown as open for trading", () => {
    const { ask, rerender } = watch([CLOSE_AT]);
    pass(HOUR);
    expect(ask).toHaveBeenCalledTimes(1);

    // The answer says CLOSED: the screen has no close time to wait for.
    rerender({ times: [] });
    pass(DAY);

    expect(ask).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  test("a close time that has passed already is asked about at once, then at the same pace", () => {
    const { ask } = watch(["2026-10-14T14:59:00Z"]);

    pass(0);
    expect(ask).toHaveBeenCalledTimes(1);
    pass(10_000);
    expect(ask).toHaveBeenCalledTimes(2);
  });

  test("with no close time it sets no timer and asks nothing", () => {
    const { ask } = watch([]);

    pass(30 * DAY);

    expect(ask).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  test("a close time that cannot be read is left out", () => {
    const { ask } = watch(["soon", CLOSE_AT]);

    pass(HOUR - 1);
    expect(ask).not.toHaveBeenCalled();
    pass(1);
    expect(ask).toHaveBeenCalledTimes(1);
  });

  test("is cleared when the screen goes away", () => {
    const { ask, unmount } = watch([CLOSE_AT]);

    unmount();
    pass(DAY);

    expect(ask).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  test("a close time further away than one timer holds is waited for in pieces, and asked about once", () => {
    const set = vi.spyOn(window, "setTimeout");
    // A market may run for 90 days; a browser's timer holds a little under 25.
    const { ask } = watch(["2026-12-13T15:00:00Z"]);

    // No timer is asked to wait longer than it holds: one that is fires at once, and the
    // wait would be a timer that fires over and over until the close time.
    const waits = set.mock.calls.map(([, wait]) => wait ?? 0);
    expect(waits.some((wait) => wait > DAY)).toBe(true);
    for (const wait of waits) expect(wait).toBeLessThanOrEqual(LONGEST_TIMER_MS);

    pass(59 * DAY);
    expect(ask).not.toHaveBeenCalled();

    pass(DAY);
    expect(ask).toHaveBeenCalledTimes(1);
  });

  test("calls the question as it is now, without setting the timer again for it", () => {
    const first = vi.fn();
    const second = vi.fn();
    const { rerender } = renderHookWithProviders(
      ({ ask }: { ask: () => void }) => useAskAtClose([CLOSE_AT], ask),
      { initialProps: { ask: first } },
    );
    const timers = vi.getTimerCount();

    rerender({ ask: second });
    expect(vi.getTimerCount()).toBe(timers);
    pass(HOUR);

    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });
});
