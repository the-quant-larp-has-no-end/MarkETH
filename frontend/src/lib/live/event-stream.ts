"use client";

import { useEffect, useEffectEvent, useState } from "react";

export type EventStreamOptions = {
  /** The address of the stream, or null while there is nothing to follow. */
  url: string | null;
  /** The names of the events to listen for (`event:` lines of the stream). */
  events: readonly string[];
  /** Called for every event that arrives, with its name and the JSON of its `data:`. */
  onEvent: (name: string, data: unknown) => void;
  /**
   * The polling fallback. Called at once when the stream is lost and then every
   * `pollSeconds` until it is back: the place to ask the API for what the stream would
   * have brought.
   */
  onPoll: () => void;
  /** `live_poll_fallback_seconds` of `/config`. */
  pollSeconds: number;
};

/**
 * Follows a server-sent event stream and keeps working when it fails (DESIGN.md §14,
 * "Live data").
 *
 * While the stream is open, events arrive through `onEvent`. If it ends or answers an
 * error, `onPoll` is called at once and then every `pollSeconds`, and each time the
 * stream is tried again. Nothing here shows an error: with the stream down a screen is a
 * little slower, not broken.
 *
 * The first poll doubles as a check of the portal's session. A stream that fails cannot
 * say why; the poll is an ordinary API call, and if the portal answers that one with its
 * 401, the page reloads (`call()`).
 *
 * The polls keep their own pace. A stream that opens and ends again at once (a proxy that
 * cuts it) fails every few seconds, and each failure would otherwise ask the API at once:
 * `onPoll` is never called sooner than `pollSeconds` after the call before. A browser
 * without event streams is treated as one whose stream is down for good.
 *
 * Returns `live`: true while the stream is open.
 */
export function useEventStream(options: EventStreamOptions): { live: boolean } {
  const { url, events, pollSeconds } = options;
  const [live, setLive] = useState(false);
  const received = useEffectEvent(options.onEvent);
  const poll = useEffectEvent(options.onPoll);
  const eventList = events.join("\n");

  useEffect(() => {
    if (url === null) return;

    let source: EventSource | null = null;
    let open = false;
    let timer: number | null = null;
    let polledAt = Number.NEGATIVE_INFINITY;
    const everyMs = pollSeconds * 1000;

    const listeners = eventList.split("\n").map((name) => {
      const listener = (event: Event) => {
        let data: unknown;
        try {
          data = JSON.parse((event as MessageEvent<string>).data);
        } catch {
          return; // A frame that is no JSON carries nothing to show.
        }
        received(name, data);
      };
      return [name, listener] as const;
    });

    const pollNow = () => {
      polledAt = Date.now();
      poll();
    };

    /** The stream is not there: ask instead, now and then at the pace of `pollSeconds`. */
    const fallBack = () => {
      if (timer !== null) return;
      if (Date.now() - polledAt >= everyMs) pollNow();
      timer = window.setInterval(tick, everyMs);
    };

    const connect = () => {
      if (source !== null || typeof EventSource === "undefined") return;
      const next = new EventSource(url);
      source = next;
      next.onopen = () => {
        open = true;
        setLive(true);
        if (timer !== null) {
          window.clearInterval(timer);
          timer = null;
        }
      };
      next.onerror = () => {
        // An HTTP error ends a stream for good; a dropped connection is retried by the
        // browser. Only the first leaves nothing behind to wait for.
        if (next.readyState === EventSource.CLOSED) {
          next.close();
          if (source === next) source = null;
        }
        open = false;
        setLive(false);
        fallBack();
      };
      for (const [name, listener] of listeners) next.addEventListener(name, listener);
    };

    const tick = () => {
      pollNow();
      connect();
    };

    // A phone drops streams in the background; do not wait for the next tick to notice.
    const wake = () => {
      if (!open && timer !== null && document.visibilityState === "visible") tick();
    };

    connect();
    if (typeof EventSource === "undefined") {
      // Nothing will ever open: poll from the start. The screen has just read what it
      // shows, so the first poll is one interval away.
      polledAt = Date.now();
      fallBack();
    }
    document.addEventListener("visibilitychange", wake);
    window.addEventListener("online", wake);

    return () => {
      document.removeEventListener("visibilitychange", wake);
      window.removeEventListener("online", wake);
      if (timer !== null) window.clearInterval(timer);
      source?.close();
      source = null;
      setLive(false);
    };
  }, [url, eventList, pollSeconds]);

  return { live };
}
