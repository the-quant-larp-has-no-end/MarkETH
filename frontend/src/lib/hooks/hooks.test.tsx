import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { toApiError } from "@/lib/api/errors";
import { REDUCED_MOTION_QUERY, WIDE_QUERY } from "@/lib/design";
import { setMediaQuery, setViewport } from "@/test/media";

import { useRetryAfter, useSecondsUntil } from "./use-countdown";
import { useDebouncedValue } from "./use-debounced-value";
import { useMediaQuery, usePrefersReducedMotion } from "./use-media-query";
import { useNow } from "./use-now";
import { useThrottledValue } from "./use-throttled-value";

describe("useMediaQuery", () => {
  test("says whether the query matches, and follows it", () => {
    const { result } = renderHook(() => useMediaQuery(WIDE_QUERY));
    expect(result.current).toBe(false);

    act(() => setViewport("wide"));
    expect(result.current).toBe(true);

    act(() => setViewport("phone"));
    expect(result.current).toBe(false);
  });

  test("a test starts with reduced motion, and can turn it off", () => {
    const { result } = renderHook(() => usePrefersReducedMotion());
    expect(result.current).toBe(true);

    act(() => setMediaQuery(REDUCED_MOTION_QUERY, false));
    expect(result.current).toBe(false);
  });
});

describe("with a clock", () => {
  beforeEach(() => {
    vi.useFakeTimers({ now: new Date("2026-10-11T10:00:00.000Z") });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  test("useNow moves forward in steps and renders the same thing twice in a row", () => {
    const { result } = renderHook(() => useNow(60_000));
    const start = result.current;
    expect(start).toBe(new Date("2026-10-11T10:00:00Z").getTime());

    act(() => vi.advanceTimersByTime(30_000));
    expect(result.current).toBe(start);

    act(() => vi.advanceTimersByTime(60_000));
    expect(result.current).toBe(start! + 60_000);
  });

  test("useNow does not tick while it is switched off", () => {
    let renders = 0;
    renderHook(() => {
      renders += 1;
      return useNow(1000, false);
    });
    const before = renders;

    act(() => vi.advanceTimersByTime(10_000));

    expect(renders).toBe(before);
  });

  test("useSecondsUntil counts down whole seconds to zero and never reaches it early", () => {
    const deadline = Date.now() + 2_500;
    const { result } = renderHook(() => useSecondsUntil(deadline));
    expect(result.current).toBe(3);

    act(() => vi.advanceTimersByTime(600));
    expect(result.current).toBe(2);

    act(() => vi.advanceTimersByTime(1_800));
    expect(result.current).toBe(1);

    act(() => vi.advanceTimersByTime(200));
    expect(result.current).toBe(0);

    act(() => vi.advanceTimersByTime(60_000));
    expect(result.current).toBe(0);
  });

  test("useSecondsUntil is zero without a deadline", () => {
    const { result } = renderHook(() => useSecondsUntil(null));
    expect(result.current).toBe(0);
  });

  test("useRetryAfter counts from when the RATE_LIMITED answer arrived", () => {
    const limited = toApiError(429, {
      code: "RATE_LIMITED",
      message: "Too many attempts. Try again later.",
      details: { retry_after_seconds: 30 },
    });
    // The screen shows the error two seconds after it arrived.
    vi.advanceTimersByTime(2_000);

    const { result } = renderHook(() => useRetryAfter(limited));
    expect(result.current).toBe(28);

    act(() => vi.advanceTimersByTime(28_000));
    expect(result.current).toBe(0);
  });

  test("useRetryAfter is zero for any other error and for none", () => {
    const other = toApiError(503, { code: "SERVICE_UNAVAILABLE", message: "x", details: {} });

    expect(renderHook(() => useRetryAfter(other)).result.current).toBe(0);
    expect(renderHook(() => useRetryAfter(null)).result.current).toBe(0);
  });

  test("useDebouncedValue passes a value on once it has stopped changing", () => {
    const { result, rerender } = renderHook(({ value }) => useDebouncedValue(value, 300), {
      initialProps: { value: "25" },
    });
    expect(result.current).toBe("25");

    rerender({ value: "50" });
    act(() => vi.advanceTimersByTime(200));
    rerender({ value: "100" });
    act(() => vi.advanceTimersByTime(200));
    // 400 ms have passed, but only 200 since the last change.
    expect(result.current).toBe("25");

    act(() => vi.advanceTimersByTime(100));
    expect(result.current).toBe("100");
  });

  test("useThrottledValue passes the first change at once and then one per interval, ending on the latest", () => {
    const { result, rerender } = renderHook(({ value }) => useThrottledValue(value, 10_000), {
      initialProps: { value: "a" },
    });

    rerender({ value: "b" });
    act(() => vi.advanceTimersByTime(1));
    expect(result.current).toBe("b");

    rerender({ value: "c" });
    rerender({ value: "d" });
    act(() => vi.advanceTimersByTime(9_000));
    expect(result.current).toBe("b");

    act(() => vi.advanceTimersByTime(1_100));
    expect(result.current).toBe("d");
  });
});
