import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { installFakeEventSource } from "@/test/fake-event-source";

import { type EventStreamOptions, useEventStream } from "./event-stream";

const URL = "/api/markets/3f6c2f0e/stream";

function follow(overrides: Partial<EventStreamOptions> = {}) {
  const onEvent = vi.fn();
  const onPoll = vi.fn();
  const view = renderHook((props: Partial<EventStreamOptions>) =>
    useEventStream({
      url: URL,
      events: ["quote", "status"],
      pollSeconds: 10,
      onEvent,
      onPoll,
      ...overrides,
      ...props,
    }),
  );
  return { ...view, onEvent, onPoll };
}

describe("useEventStream", () => {
  let streams: ReturnType<typeof installFakeEventSource>;

  beforeEach(() => {
    vi.useFakeTimers();
    streams = installFakeEventSource();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  test("opens the stream on the page's own origin and is live once it is open", () => {
    const { result } = follow();

    expect(streams.instances).toHaveLength(1);
    expect(streams.latest().url).toBe(URL);
    expect(result.current.live).toBe(false);

    act(() => streams.latest().open());

    expect(result.current.live).toBe(true);
  });

  test("hands on each named event with the JSON of its data", () => {
    const { onEvent } = follow();
    act(() => streams.latest().open());

    act(() => streams.latest().emit("quote", { version: 8, p_yes: 0.559 }));
    act(() => streams.latest().emit("status", { status: "HALTED" }));

    expect(onEvent).toHaveBeenNthCalledWith(1, "quote", { version: 8, p_yes: 0.559 });
    expect(onEvent).toHaveBeenNthCalledWith(2, "status", { status: "HALTED" });
  });

  test("skips a frame that is no JSON", () => {
    const { onEvent } = follow();
    act(() => streams.latest().open());

    act(() => streams.latest().emitRaw("quote", "not json"));

    expect(onEvent).not.toHaveBeenCalled();
  });

  test("does not poll while the stream is open", () => {
    const { onPoll } = follow();
    act(() => streams.latest().open());

    act(() => vi.advanceTimersByTime(60_000));

    expect(onPoll).not.toHaveBeenCalled();
    expect(streams.instances).toHaveLength(1);
  });

  test("when the stream ends, polls at once and then every poll interval, trying the stream again each time", () => {
    const { result, onPoll } = follow();
    act(() => streams.latest().open());

    act(() => streams.latest().fail());

    expect(result.current.live).toBe(false);
    // At once: this first poll is also what notices an ended portal session.
    expect(onPoll).toHaveBeenCalledTimes(1);
    expect(streams.instances).toHaveLength(1);

    act(() => vi.advanceTimersByTime(10_000));
    expect(onPoll).toHaveBeenCalledTimes(2);
    expect(streams.instances).toHaveLength(2);

    act(() => streams.latest().fail());
    act(() => vi.advanceTimersByTime(10_000));
    expect(onPoll).toHaveBeenCalledTimes(3);
    expect(streams.instances).toHaveLength(3);
  });

  test("a stream that never opened falls back to polling just the same", () => {
    const { onPoll } = follow();

    act(() => streams.latest().fail());

    expect(onPoll).toHaveBeenCalledTimes(1);
  });

  test("stops polling as soon as the stream is back", () => {
    const { result, onPoll } = follow();
    act(() => streams.latest().fail());
    act(() => vi.advanceTimersByTime(10_000));
    expect(onPoll).toHaveBeenCalledTimes(2);

    act(() => streams.latest().open());
    act(() => vi.advanceTimersByTime(60_000));

    expect(result.current.live).toBe(true);
    expect(onPoll).toHaveBeenCalledTimes(2);
    expect(streams.instances).toHaveLength(2);
  });

  test("a dropped connection is left to the browser to retry: polling starts, no second stream is opened", () => {
    const { onPoll } = follow();
    act(() => streams.latest().open());

    act(() => streams.latest().fail({ closed: false }));
    act(() => vi.advanceTimersByTime(20_000));

    expect(onPoll).toHaveBeenCalledTimes(3);
    expect(streams.instances).toHaveLength(1);
    expect(streams.latest().closed).toBe(false);
  });

  test("repeated errors of a stream that is already down do not poll more often", () => {
    const { onPoll } = follow();
    act(() => streams.latest().fail({ closed: false }));

    act(() => streams.latest().fail({ closed: false }));
    act(() => streams.latest().fail({ closed: false }));

    expect(onPoll).toHaveBeenCalledTimes(1);
  });

  test("a stream that opens and ends again at once does not make the polls come faster", () => {
    const { onPoll } = follow();
    // The browser reconnects by itself every three seconds, and each time the stream is
    // cut right after it opened.
    const cut = () => {
      act(() => streams.latest().open());
      act(() => streams.latest().fail({ closed: false }));
    };

    cut();
    expect(onPoll).toHaveBeenCalledTimes(1);

    for (let second = 3; second <= 9; second += 3) {
      act(() => vi.advanceTimersByTime(3_000));
      cut();
    }
    expect(onPoll).toHaveBeenCalledTimes(1);

    // Twelve seconds after the first poll, which is more than the interval of ten.
    act(() => vi.advanceTimersByTime(3_000));
    cut();
    expect(onPoll).toHaveBeenCalledTimes(2);
    expect(streams.instances).toHaveLength(1);
  });

  test("takes its poll interval from the caller", () => {
    const { onPoll } = follow({ pollSeconds: 3 });
    act(() => streams.latest().fail());

    act(() => vi.advanceTimersByTime(9_000));

    expect(onPoll).toHaveBeenCalledTimes(4);
  });

  test("when the page comes back into view while the stream is down, it does not wait for the next tick", () => {
    const { onPoll } = follow();
    act(() => streams.latest().fail());
    expect(onPoll).toHaveBeenCalledTimes(1);

    act(() => document.dispatchEvent(new Event("visibilitychange")));

    expect(onPoll).toHaveBeenCalledTimes(2);
    expect(streams.instances).toHaveLength(2);
  });

  test("closes the stream and stops polling when the screen goes away", () => {
    const { unmount, onPoll } = follow();
    act(() => streams.latest().fail({ closed: false }));
    const stream = streams.latest();

    unmount();
    act(() => vi.advanceTimersByTime(60_000));

    expect(stream.closed).toBe(true);
    expect(onPoll).toHaveBeenCalledTimes(1);
  });

  test("follows nothing while it has no address, and starts when it gets one", () => {
    const { rerender, result } = follow({ url: null });
    expect(streams.instances).toHaveLength(0);
    expect(result.current.live).toBe(false);

    rerender({ url: URL });

    expect(streams.instances).toHaveLength(1);
  });

  test("uses the latest handlers without opening the stream again", () => {
    const { rerender, onEvent } = follow();
    act(() => streams.latest().open());
    const later = vi.fn();

    rerender({ onEvent: later });
    act(() => streams.latest().emit("quote", { version: 9 }));

    expect(streams.instances).toHaveLength(1);
    expect(later).toHaveBeenCalledWith("quote", { version: 9 });
    expect(onEvent).not.toHaveBeenCalled();
  });

  test("where the browser has no EventSource, it polls at the pace of the interval from the start", () => {
    vi.stubGlobal("EventSource", undefined);

    const { result, onPoll, unmount } = follow();

    expect(result.current.live).toBe(false);
    // The screen has just read what it shows.
    expect(onPoll).not.toHaveBeenCalled();

    act(() => vi.advanceTimersByTime(10_000));
    expect(onPoll).toHaveBeenCalledTimes(1);
    act(() => vi.advanceTimersByTime(10_000));
    expect(onPoll).toHaveBeenCalledTimes(2);

    unmount();
    act(() => vi.advanceTimersByTime(60_000));
    expect(onPoll).toHaveBeenCalledTimes(2);
  });
});
