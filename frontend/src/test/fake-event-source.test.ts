import { expect, test, vi } from "vitest";

import { installFakeEventSource } from "./fake-event-source";

/** What a listener of the app gets: the event's name and the JSON of its `data:` line. */
function listen(stream: EventSource, name: string) {
  const heard = vi.fn();
  stream.addEventListener(name, (event) => heard(event.type, JSON.parse(event.data as string)));
  return heard;
}

test("the app's EventSource is the fake, and every stream it opens is kept with its address", () => {
  const streams = installFakeEventSource();

  const first = new EventSource("/api/stream?tab=tab-0001&topic=leaderboard&topic=markets");
  const second = new EventSource("/api/stream?tab=tab-0001&topic=markets");

  expect(streams.instances.map((stream) => stream.url)).toEqual([first.url, second.url]);
  expect(streams.latest().url).toBe("/api/stream?tab=tab-0001&topic=markets");
  expect(streams.instances[0]?.topics).toEqual(["leaderboard", "markets"]);
  expect(streams.latest().topics).toEqual(["markets"]);
  expect(streams.latest().tab).toBe("tab-0001");
});

test("a stream without a query names no topic and no tab", () => {
  const streams = installFakeEventSource();

  new EventSource("/api/stream");

  expect(streams.latest().topics).toEqual([]);
  expect(streams.latest().tab).toBeNull();
});

test("an event reaches the listeners of its name and no others", () => {
  const streams = installFakeEventSource();
  const stream = new EventSource("/api/stream?tab=tab-0001&topic=markets");
  const quotes = listen(stream, "market.quote");
  const statuses = listen(stream, "market.status");

  streams.latest().emit("market.quote", { type: "market.quote", version: 8 });

  expect(quotes).toHaveBeenCalledExactlyOnceWith("market.quote", {
    type: "market.quote",
    version: 8,
  });
  expect(statuses).not.toHaveBeenCalled();
});

test("send plays an event under the name its body carries as type", () => {
  const streams = installFakeEventSource();
  const stream = new EventSource("/api/stream?tab=tab-0001");
  const changes = listen(stream, "me.changed");

  streams.latest().send({ type: "me.changed", ts: "2026-10-04T10:00:00Z" });

  expect(changes).toHaveBeenCalledExactlyOnceWith("me.changed", {
    type: "me.changed",
    ts: "2026-10-04T10:00:00Z",
  });
});

test("emitRaw hands the text over as it is, JSON or not", () => {
  const streams = installFakeEventSource();
  const stream = new EventSource("/api/stream?tab=tab-0001");
  const heard = vi.fn();
  stream.addEventListener("beat", (event) => heard(event.data));

  streams.latest().emitRaw("beat", "not json");

  expect(heard).toHaveBeenCalledExactlyOnceWith("not json");
});

test("held lists the streams the app opened and has not closed", () => {
  const streams = installFakeEventSource();
  const first = new EventSource("/api/stream?tab=tab-0001&topic=markets");
  const second = new EventSource("/api/stream?tab=tab-0001&topic=leaderboard");

  expect(streams.held().map((stream) => stream.url)).toEqual([first.url, second.url]);

  first.close();

  expect(streams.instances).toHaveLength(2);
  expect(streams.held().map((stream) => stream.url)).toEqual([second.url]);
  expect(streams.instances[0]?.closed).toBe(true);
  expect(first.readyState).toBe(EventSource.CLOSED);
});

test("open and fail move the stream through the states a browser reports", () => {
  const streams = installFakeEventSource();
  const stream = new EventSource("/api/stream?tab=tab-0001");
  const opened = vi.fn();
  const failed = vi.fn();
  stream.onopen = opened;
  stream.onerror = failed;

  expect(stream.readyState).toBe(EventSource.CONNECTING);
  streams.latest().open();
  expect(stream.readyState).toBe(EventSource.OPEN);
  expect(opened).toHaveBeenCalledOnce();

  // A dropped connection: the browser retries by itself.
  streams.latest().fail({ closed: false });
  expect(stream.readyState).toBe(EventSource.CONNECTING);
  // An HTTP error: the stream ended for good.
  streams.latest().fail();
  expect(stream.readyState).toBe(EventSource.CLOSED);
  expect(failed).toHaveBeenCalledTimes(2);
});

test("latest fails the test that asks for a stream the app has not opened", () => {
  const streams = installFakeEventSource();

  expect(() => streams.latest()).toThrow("The app has not opened an event stream.");
});
