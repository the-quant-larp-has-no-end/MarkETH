import { afterEach, beforeEach, describe, expect, type MockInstance, test, vi } from "vitest";

import { installFakeEventSource } from "@/test/fake-event-source";
import {
  beatEvent,
  config,
  MARKET_ID,
  marketQuoteEvent,
  meChangedEvent,
  readyEvent,
} from "@/test/fixtures";

import { LiveClient, type LiveClientOptions } from "./client";
import { LIVE_EVENT_NAMES } from "./events";
import { liveTopic } from "./topics";

const HEARTBEAT_MS = config().live_heartbeat_seconds * 1000;
const POLL_MS = config().live_poll_fallback_seconds * 1000;

const clients: LiveClient[] = [];

/** A client with the paces of `config()`, stopped after the test. */
function make(overrides: Partial<LiveClientOptions> = {}) {
  const onEvent = vi.fn();
  const onPoll = vi.fn();
  const client = new LiveClient({
    heartbeatSeconds: config().live_heartbeat_seconds,
    pollSeconds: config().live_poll_fallback_seconds,
    onEvent,
    onPoll,
    ...overrides,
  });
  clients.push(client);
  return { client, onEvent, onPoll };
}

/** The page is hidden, or shown again. */
function show(state: "visible" | "hidden") {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
  document.dispatchEvent(new Event("visibilitychange"));
}

describe("LiveClient", () => {
  let streams: ReturnType<typeof installFakeEventSource>;
  let random: MockInstance<() => number>;

  beforeEach(() => {
    vi.useFakeTimers();
    streams = installFakeEventSource();
    // The first poll after a loss comes after this part of the interval.
    random = vi.spyOn(Math, "random").mockReturnValue(0.5);
  });
  afterEach(() => {
    for (const client of clients.splice(0)) client.stop();
    Reflect.deleteProperty(document, "visibilityState");
    vi.useRealTimers();
  });

  describe("the address", () => {
    test("is the stream's, with the tab's id and no topic while none is wanted", () => {
      const { client } = make();

      client.start();

      expect(streams.instances).toHaveLength(1);
      const address = new URL(streams.latest().url, window.location.origin);
      expect(address.pathname).toBe("/api/stream");
      expect([...address.searchParams.keys()]).toEqual(["tab"]);
      expect(streams.latest().tab).toMatch(/^[A-Za-z0-9_-]{8,36}$/);
    });

    test("names the wanted topics in sorted order, each once, and no cursor", () => {
      const { client } = make();
      client.follow(liveTopic.markets());
      client.follow(liveTopic.market(MARKET_ID));
      client.follow(liveTopic.leaderboard());
      client.follow(liveTopic.broadcast(MARKET_ID));
      client.follow(liveTopic.markets());

      client.start();

      expect(streams.latest().topics).toEqual([
        `broadcast:${MARKET_ID}`,
        "leaderboard",
        `market:${MARKET_ID}`,
        "markets",
      ]);
      const names = [...new URL(streams.latest().url, window.location.origin).searchParams.keys()];
      expect(new Set(names)).toEqual(new Set(["tab", "topic"]));
    });

    test("names the same tab on every stream of the page, whichever client opens it", () => {
      const first = make();
      first.client.start();
      streams.latest().fail();
      vi.advanceTimersByTime(POLL_MS);
      const second = make();
      second.client.start();

      expect(streams.instances).toHaveLength(3);
      expect(new Set(streams.instances.map((stream) => stream.tab)).size).toBe(1);
    });

    test("is not opened before the client is started", () => {
      const { client } = make();
      client.follow(liveTopic.markets());

      vi.advanceTimersByTime(60_000);

      expect(streams.instances).toHaveLength(0);
      expect(client.live).toBe(false);
    });
  });

  describe("events", () => {
    test("ready makes it live, and an open stream alone does not", () => {
      const { client } = make();
      const changed = vi.fn();
      client.subscribe(changed);
      client.start();

      streams.latest().open();
      expect(client.live).toBe(false);

      streams.latest().send(readyEvent());

      expect(client.live).toBe(true);
      expect(changed).toHaveBeenCalledTimes(1);
    });

    test("hands on every event with its body, ready and beat among them", () => {
      const { client, onEvent } = make();
      client.start();

      streams.latest().send(readyEvent());
      streams.latest().send(marketQuoteEvent({ version: 9 }));
      streams.latest().send(meChangedEvent());
      streams.latest().send(beatEvent());

      expect(onEvent.mock.calls.map(([event]) => event)).toEqual([
        readyEvent(),
        marketQuoteEvent({ version: 9 }),
        meChangedEvent(),
        beatEvent(),
      ]);
    });

    test("listens for every name of the contract, those of a live stream and of its chat among them", () => {
      const { client, onEvent } = make();
      client.start();

      for (const name of LIVE_EVENT_NAMES) streams.latest().emit(name, { type: name });

      expect(onEvent.mock.calls.map(([event]) => event)).toEqual(
        LIVE_EVENT_NAMES.map((name) => ({ type: name })),
      );
      expect(LIVE_EVENT_NAMES).toEqual(
        expect.arrayContaining(["broadcast.changed", "chat.message", "chat.removed"]),
      );
    });

    test("hands on every ready: each stream that opens is read after once", () => {
      const { client, onEvent } = make();
      client.start();
      streams.latest().send(readyEvent());

      streams.latest().fail({ closed: false });
      streams.latest().send(readyEvent({ ts: "2026-10-04T10:01:00Z" }));

      expect(onEvent).toHaveBeenCalledTimes(2);
      expect(onEvent).toHaveBeenLastCalledWith(readyEvent({ ts: "2026-10-04T10:01:00Z" }));
    });

    test("drops a frame that is no JSON, and a body that is not the event it travelled as", () => {
      const { client, onEvent } = make();
      client.start();

      streams.latest().emitRaw("market.quote", "not json");
      streams.latest().emitRaw("market.quote", "null");
      streams.latest().emit("market.quote", meChangedEvent());
      // Not a ready either: the stream is not live by it.
      streams.latest().emitRaw("ready", "{");

      expect(onEvent).not.toHaveBeenCalled();
      expect(client.live).toBe(false);
    });

    test("stops telling a listener that was taken away", () => {
      const { client } = make();
      const changed = vi.fn();
      const end = client.subscribe(changed);
      client.start();

      end();
      streams.latest().send(readyEvent());

      expect(changed).not.toHaveBeenCalled();
    });
  });

  describe("a change of the wanted topics", () => {
    test("exchanges the stream once, 50 ms after the last change, and live does not flip", () => {
      const { client, onPoll } = make();
      const release = client.follow(liveTopic.markets());
      client.start();
      const first = streams.latest();
      first.send(readyEvent());
      const changed = vi.fn();
      client.subscribe(changed);

      // A navigation: one topic goes and another comes in the same moment.
      release();
      client.follow(liveTopic.market(MARKET_ID));
      vi.advanceTimersByTime(49);
      client.follow(liveTopic.leaderboard());
      vi.advanceTimersByTime(49);

      expect(streams.instances).toHaveLength(1);
      expect(first.closed).toBe(false);

      vi.advanceTimersByTime(1);

      expect(streams.instances).toHaveLength(2);
      expect(first.closed).toBe(true);
      expect(streams.latest().topics).toEqual(["leaderboard", `market:${MARKET_ID}`]);
      expect(streams.latest().tab).toBe(first.tab);
      expect(streams.held()).toHaveLength(1);
      expect(client.live).toBe(true);

      streams.latest().send(readyEvent());

      expect(client.live).toBe(true);
      expect(changed).not.toHaveBeenCalled();
      expect(onPoll).not.toHaveBeenCalled();
    });

    test("exchanges nothing when the set is the one the stream was opened with", () => {
      const { client } = make();
      client.follow(liveTopic.markets());
      client.start();

      // A second follower of a topic, and a topic that comes and goes within the 50 ms.
      const second = client.follow(liveTopic.markets());
      const passing = client.follow(liveTopic.leaderboard());
      passing();
      vi.advanceTimersByTime(1_000);
      second();
      vi.advanceTimersByTime(1_000);

      expect(streams.instances).toHaveLength(1);
      expect(streams.latest().closed).toBe(false);
    });

    test("keeps a topic for as long as one of its followers holds it, and lets it go once", () => {
      const { client } = make();
      const first = client.follow(liveTopic.markets());
      const second = client.follow(liveTopic.markets());
      client.start();

      first();
      first();
      vi.advanceTimersByTime(1_000);
      expect(client.topics).toEqual(["markets"]);
      expect(streams.instances).toHaveLength(1);

      second();
      vi.advanceTimersByTime(50);

      expect(client.topics).toEqual([]);
      expect(streams.latest().topics).toEqual([]);
      expect(streams.instances).toHaveLength(2);
    });

    test("ignores what a stream that was let go still sends", () => {
      const { client, onEvent } = make();
      client.start();
      const first = streams.latest();
      first.send(readyEvent());
      client.follow(liveTopic.markets());
      vi.advanceTimersByTime(50);
      onEvent.mockClear();

      first.send(marketQuoteEvent());
      first.fail();

      expect(onEvent).not.toHaveBeenCalled();
      expect(client.live).toBe(true);
    });

    test("exchanges a stream the browser is retrying, and goes on polling until it is ready", () => {
      const { client, onPoll } = make();
      client.start();
      streams.latest().send(readyEvent());
      streams.latest().fail({ closed: false });

      client.follow(liveTopic.markets());
      vi.advanceTimersByTime(50);

      expect(streams.instances).toHaveLength(2);
      expect(streams.instances[0]!.closed).toBe(true);
      expect(streams.latest().topics).toEqual(["markets"]);
      expect(client.live).toBe(false);

      vi.advanceTimersByTime(POLL_MS / 2);
      expect(onPoll).toHaveBeenCalledTimes(1);
    });

    test("opens nothing while there is no stream at hand: the next attempt names what is wanted then", () => {
      const { client } = make();
      client.start();
      streams.latest().fail();

      client.follow(liveTopic.markets());
      vi.advanceTimersByTime(50);
      expect(streams.instances).toHaveLength(1);

      vi.advanceTimersByTime(POLL_MS / 2);

      expect(streams.instances).toHaveLength(2);
      expect(streams.latest().topics).toEqual(["markets"]);
    });
  });

  describe("a hidden page", () => {
    test("holds no stream and no timer, and opens its stream when it is shown again", () => {
      const { client, onPoll } = make();
      client.follow(liveTopic.markets());
      client.start();
      const first = streams.latest();
      first.send(readyEvent());

      show("hidden");

      expect(first.closed).toBe(true);
      expect(streams.held()).toHaveLength(0);
      expect(client.live).toBe(false);

      // Neither the watchdog nor a poll nor a change of topics does anything meanwhile.
      client.follow(liveTopic.leaderboard());
      vi.advanceTimersByTime(10 * 60_000);

      expect(streams.instances).toHaveLength(1);
      expect(onPoll).not.toHaveBeenCalled();

      show("visible");

      expect(streams.instances).toHaveLength(2);
      expect(streams.held()).toHaveLength(1);
      expect(streams.latest().topics).toEqual(["leaderboard", "markets"]);
      expect(streams.latest().tab).toBe(first.tab);
      expect(client.live).toBe(false);

      streams.latest().send(readyEvent());
      expect(client.live).toBe(true);
    });

    test("does not poll, also when its stream was lost before it was hidden", () => {
      const { client, onPoll } = make();
      client.start();
      streams.latest().fail();

      show("hidden");
      vi.advanceTimersByTime(10 * 60_000);

      expect(onPoll).not.toHaveBeenCalled();
      expect(streams.instances).toHaveLength(1);
    });

    test("that is started hidden opens its stream only when it is shown", () => {
      show("hidden");
      const { client } = make();

      client.start();
      vi.advanceTimersByTime(60_000);
      expect(streams.instances).toHaveLength(0);

      show("visible");
      expect(streams.instances).toHaveLength(1);
    });

    test("that is shown twice opens one stream", () => {
      const { client } = make();
      client.start();

      show("visible");
      show("visible");

      expect(streams.instances).toHaveLength(1);
    });
  });

  describe("the heartbeat", () => {
    test("no event for two heartbeats closes the stream and opens it again at once", () => {
      const { client, onPoll } = make();
      client.follow(liveTopic.markets());
      client.start();
      const first = streams.latest();
      first.send(readyEvent());

      vi.advanceTimersByTime(2 * HEARTBEAT_MS - 1);
      expect(streams.instances).toHaveLength(1);
      expect(client.live).toBe(true);

      vi.advanceTimersByTime(1);

      expect(first.closed).toBe(true);
      expect(streams.instances).toHaveLength(2);
      expect(streams.latest().topics).toEqual(["markets"]);
      // A dead path is a loss, though nothing asks yet: the stream that follows says more.
      expect(client.live).toBe(false);
      expect(onPoll).not.toHaveBeenCalled();

      streams.latest().send(readyEvent());
      expect(client.live).toBe(true);
    });

    test("a beat, like any event, says that the stream is alive", () => {
      const { client } = make();
      client.start();
      streams.latest().send(readyEvent());

      for (let beats = 0; beats < 8; beats += 1) {
        vi.advanceTimersByTime(HEARTBEAT_MS);
        streams.latest().send(beatEvent());
      }
      vi.advanceTimersByTime(HEARTBEAT_MS);
      streams.latest().send(marketQuoteEvent());
      vi.advanceTimersByTime(HEARTBEAT_MS);

      expect(streams.instances).toHaveLength(1);
      expect(client.live).toBe(true);
    });

    test("a stream that was opened and says nothing for two heartbeats is lost: the tab polls", () => {
      const { client, onPoll } = make();
      client.start();
      streams.latest().open();

      vi.advanceTimersByTime(2 * HEARTBEAT_MS);

      expect(streams.latest().closed).toBe(true);
      expect(streams.instances).toHaveLength(1);

      vi.advanceTimersByTime(POLL_MS / 2);

      expect(onPoll).toHaveBeenCalledTimes(1);
      expect(streams.instances).toHaveLength(2);
    });

    test("the stream that follows a dead path and stays silent is lost as well", () => {
      const { client, onPoll } = make();
      client.start();
      streams.latest().send(readyEvent());
      vi.advanceTimersByTime(2 * HEARTBEAT_MS);
      expect(streams.instances).toHaveLength(2);

      vi.advanceTimersByTime(2 * HEARTBEAT_MS);
      expect(streams.latest().closed).toBe(true);
      vi.advanceTimersByTime(POLL_MS / 2);

      expect(onPoll).toHaveBeenCalledTimes(1);
    });

    test("while the tab polls, a stream that is open and silent is let go, and the next poll tries again", () => {
      const { client, onPoll } = make();
      client.start();
      streams.latest().fail();
      vi.advanceTimersByTime(POLL_MS / 2);
      expect(streams.instances).toHaveLength(2);
      const silent = streams.latest();
      // The API accepted it, and then nothing: no ready, no beat.
      silent.open();

      vi.advanceTimersByTime(2 * HEARTBEAT_MS + POLL_MS);

      expect(silent.closed).toBe(true);
      expect(streams.instances).toHaveLength(3);
      expect(streams.held()).toHaveLength(1);
      expect(client.live).toBe(false);
      // The polls went on at their pace all the while.
      expect(onPoll).toHaveBeenCalledTimes(5);
    });

    test("while the tab polls, a stream the browser is still connecting is left to it", () => {
      const { client } = make();
      client.start();
      const stream = streams.latest();
      stream.fail({ closed: false });

      vi.advanceTimersByTime(10 * HEARTBEAT_MS);

      expect(stream.closed).toBe(false);
      expect(streams.instances).toHaveLength(1);
    });
  });

  describe("a stream that cannot be held", () => {
    test("an HTTP error polls, and tries the stream again at each poll", () => {
      const { client, onPoll } = make();
      client.start();
      streams.latest().send(readyEvent());

      streams.latest().fail();

      expect(client.live).toBe(false);
      expect(streams.latest().closed).toBe(true);
      expect(onPoll).not.toHaveBeenCalled();

      vi.advanceTimersByTime(POLL_MS / 2);
      expect(onPoll).toHaveBeenCalledTimes(1);
      expect(streams.instances).toHaveLength(2);

      streams.latest().fail();
      vi.advanceTimersByTime(POLL_MS);
      expect(onPoll).toHaveBeenCalledTimes(2);
      expect(streams.instances).toHaveLength(3);
      expect(streams.held()).toHaveLength(1);
    });

    test("the first ready ends the polling", () => {
      const { client, onPoll } = make();
      client.start();
      streams.latest().fail();
      vi.advanceTimersByTime(POLL_MS / 2);
      expect(onPoll).toHaveBeenCalledTimes(1);

      streams.latest().open();
      vi.advanceTimersByTime(POLL_MS);
      // Open is not ready: the tab still asks.
      expect(onPoll).toHaveBeenCalledTimes(2);

      streams.latest().send(readyEvent());
      vi.advanceTimersByTime(2 * POLL_MS);

      expect(client.live).toBe(true);
      expect(onPoll).toHaveBeenCalledTimes(2);
      expect(streams.instances).toHaveLength(2);
    });

    test("a dropped connection polls and leaves the retrying to the browser", () => {
      const { client, onPoll } = make();
      client.start();
      const stream = streams.latest();
      stream.send(readyEvent());

      stream.fail({ closed: false });

      expect(client.live).toBe(false);
      expect(stream.closed).toBe(false);

      vi.advanceTimersByTime(3 * POLL_MS);
      expect(onPoll).toHaveBeenCalledTimes(3);
      expect(streams.instances).toHaveLength(1);

      // The browser brought it back.
      stream.send(readyEvent());
      vi.advanceTimersByTime(2 * POLL_MS);

      expect(client.live).toBe(true);
      expect(onPoll).toHaveBeenCalledTimes(3);
    });

    test("a stream the browser gave up on is tried again at the next poll", () => {
      const { client } = make();
      client.start();
      const stream = streams.latest();
      stream.fail({ closed: false });

      stream.fail();
      vi.advanceTimersByTime(POLL_MS / 2);

      expect(stream.closed).toBe(true);
      expect(streams.instances).toHaveLength(2);
    });

    test.each([
      [0, 0],
      [0.25, POLL_MS / 4],
      [0.999, POLL_MS * 0.999],
    ])("the first poll comes after a random part of one interval (%s of it)", (part, wait) => {
      random.mockReturnValue(part);
      const { client, onPoll } = make();
      client.start();

      streams.latest().fail();
      if (wait > 0) {
        vi.advanceTimersByTime(wait - 1);
        expect(onPoll).not.toHaveBeenCalled();
      }
      vi.advanceTimersByTime(wait > 0 ? 1 : 0);
      expect(onPoll).toHaveBeenCalledTimes(1);

      // Then every interval.
      vi.advanceTimersByTime(POLL_MS - 1);
      expect(onPoll).toHaveBeenCalledTimes(1);
      vi.advanceTimersByTime(1);
      expect(onPoll).toHaveBeenCalledTimes(2);
    });

    test("a stream that opens and fails at once does not poll faster than every interval", () => {
      random.mockReturnValue(0);
      const { client, onPoll } = make();
      client.start();
      streams.latest().fail();
      vi.advanceTimersByTime(0);
      expect(onPoll).toHaveBeenCalledTimes(1);

      // A proxy that cuts the stream: every attempt is ready and fails a second later.
      for (let attempt = 0; attempt < 5; attempt += 1) {
        streams.latest().send(readyEvent());
        vi.advanceTimersByTime(1_000);
        streams.latest().fail();
        vi.advanceTimersByTime(POLL_MS - 1_001);
        expect(onPoll).toHaveBeenCalledTimes(attempt + 1);
        vi.advanceTimersByTime(1);
        expect(onPoll).toHaveBeenCalledTimes(attempt + 2);
      }
    });

    test("a browser without EventSource polls from the start, and stays not live", () => {
      vi.stubGlobal("EventSource", undefined);
      const { client, onPoll } = make();

      client.start();
      expect(client.live).toBe(false);
      expect(onPoll).not.toHaveBeenCalled();

      vi.advanceTimersByTime(POLL_MS / 2);
      expect(onPoll).toHaveBeenCalledTimes(1);
      vi.advanceTimersByTime(3 * POLL_MS);

      expect(onPoll).toHaveBeenCalledTimes(4);
      expect(client.live).toBe(false);
    });

    test("when the connection is back, the next poll does not wait out its random part", () => {
      random.mockReturnValue(0.9);
      const { client, onPoll } = make();
      client.start();
      streams.latest().fail();
      vi.advanceTimersByTime(1_000);
      expect(onPoll).not.toHaveBeenCalled();

      window.dispatchEvent(new Event("online"));
      vi.advanceTimersByTime(0);

      expect(onPoll).toHaveBeenCalledTimes(1);
      expect(streams.instances).toHaveLength(2);
    });

    test("when the connection is back, a poll still comes no sooner than an interval after the one before", () => {
      const { client, onPoll } = make();
      client.start();
      streams.latest().fail();
      vi.advanceTimersByTime(POLL_MS / 2);
      expect(onPoll).toHaveBeenCalledTimes(1);

      vi.advanceTimersByTime(2_000);
      window.dispatchEvent(new Event("online"));
      vi.advanceTimersByTime(POLL_MS - 2_001);
      expect(onPoll).toHaveBeenCalledTimes(1);

      vi.advanceTimersByTime(1);
      expect(onPoll).toHaveBeenCalledTimes(2);
    });

    test("the connection coming back changes nothing while the stream is open", () => {
      const { client, onPoll } = make();
      client.start();
      streams.latest().send(readyEvent());

      window.dispatchEvent(new Event("online"));
      vi.advanceTimersByTime(HEARTBEAT_MS);

      expect(onPoll).not.toHaveBeenCalled();
      expect(streams.instances).toHaveLength(1);
    });
  });

  describe("stop", () => {
    test("closes the stream, is not live, and leaves no timer and no listener behind", () => {
      const { client, onPoll, onEvent } = make();
      client.start();
      const stream = streams.latest();
      stream.send(readyEvent());
      onEvent.mockClear();

      client.stop();

      expect(stream.closed).toBe(true);
      expect(client.live).toBe(false);

      stream.send(marketQuoteEvent());
      show("hidden");
      show("visible");
      window.dispatchEvent(new Event("online"));
      vi.advanceTimersByTime(10 * 60_000);

      expect(streams.instances).toHaveLength(1);
      expect(onPoll).not.toHaveBeenCalled();
      expect(onEvent).not.toHaveBeenCalled();
      expect(vi.getTimerCount()).toBe(0);
    });

    test("stops the polling of a lost stream", () => {
      const { client, onPoll } = make();
      client.start();
      streams.latest().fail();

      client.stop();
      vi.advanceTimersByTime(10 * POLL_MS);

      expect(onPoll).not.toHaveBeenCalled();
      expect(vi.getTimerCount()).toBe(0);
    });

    test("a client that was stopped opens its stream when it is started again", () => {
      const { client } = make();
      client.follow(liveTopic.markets());
      client.start();
      client.stop();

      client.start();

      expect(streams.instances).toHaveLength(2);
      expect(streams.held()).toHaveLength(1);
      expect(streams.latest().topics).toEqual(["markets"]);
    });
  });
});
