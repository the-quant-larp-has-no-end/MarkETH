import { type QueryClient, type QueryKey, QueryObserver } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { keys } from "@/lib/api/keys";
import {
  beatEvent,
  config,
  leaderboardChangedEvent,
  MARKET_ID,
  marketQuoteEvent,
  marketStatusEvent,
  meChangedEvent,
  OTHER_MARKET_ID,
  readyEvent,
} from "@/test/fixtures";
import { createTestQueryClient } from "@/test/render";

import type { LiveEvent, LiveEventOf } from "./events";
import { createLiveRules, type LiveRulesOptions } from "./rules";
import { liveTopic } from "./topics";

const SPREAD_MS = config().live_announce_spread_ms;

const BROADCAST_ID = "5b0c7a52-3d1e-4f8a-9c60-2e7d4b1a8f33";
const MESSAGE_ID = "c1d2e3f4-0a1b-4c2d-8e3f-4a5b6c7d8e9f";

/** `broadcast.changed`: something about the live stream of a market changed. It carries no more. */
const changed = (marketId: string = MARKET_ID): LiveEventOf<"broadcast.changed"> => ({
  type: "broadcast.changed",
  market_id: marketId,
  ts: "2026-10-04T10:00:00Z",
});

const CHAT_MESSAGE: LiveEventOf<"chat.message"> = {
  type: "chat.message",
  market_id: MARKET_ID,
  broadcast_id: BROADCAST_ID,
  message: { id: MESSAGE_ID, username: "anna_m", text: "Hello from the back row", from_vis: false },
  ts: "2026-10-04T10:00:01Z",
};

const CHAT_REMOVED: LiveEventOf<"chat.removed"> = {
  type: "chat.removed",
  market_id: MARKET_ID,
  broadcast_id: BROADCAST_ID,
  message_id: MESSAGE_ID,
  ts: "2026-10-04T10:00:02Z",
};

/** What the rules name: the live stream of the market, and the two lists. */
const THE_BROADCAST = keys.markets.broadcast(MARKET_ID);
const ANOTHER_BROADCAST = keys.markets.broadcast(OTHER_MARKET_ID);
const LIVE_ONES = keys.broadcasts.live;
const ADMINS_LIST = keys.admin.broadcasts;

/** What no rule names: prices, the ranking, the caller's own, the chat, an open quote. */
const NOT_THE_RULES: readonly QueryKey[] = [
  keys.me,
  keys.positions,
  keys.ledger,
  keys.products,
  keys.markets.list(),
  keys.markets.detail(MARKET_ID),
  keys.markets.history(MARKET_ID),
  keys.markets.quote(MARKET_ID, { side: "BUY", outcome: "YES", shares: "10" }),
  keys.markets.chat(MARKET_ID),
  keys.leaderboard.list({}),
];

const ended: (() => void)[] = [];

function setUp(options: Partial<LiveRulesOptions> = {}) {
  const client = createTestQueryClient();
  const rules = createLiveRules(client, { spreadMs: SPREAD_MS, followed: () => [], ...options });
  ended.push(
    () => rules.stop(),
    () => client.clear(),
  );
  return { client, rules };
}

/**
 * A read that is in the cache with "held" and on screen: somebody observes it. Returns the
 * function that reads it again, which answers "read again" unless the test says otherwise.
 */
function onScreen(client: QueryClient, queryKey: QueryKey) {
  client.setQueryData(queryKey, "held");
  const read = vi.fn((): Promise<string> => Promise.resolve("read again"));
  const observer = new QueryObserver(client, { queryKey, queryFn: read, refetchOnMount: false });
  ended.push(observer.subscribe(() => {}));
  return read;
}

/** An answer the test gives when it chooses. */
function later() {
  let answer!: (value: string) => void;
  const promise = new Promise<string>((resolve) => {
    answer = resolve;
  });
  return { promise, answer };
}

/** Lets the clock run, and every read that is out be answered. */
const pass = (ms: number) => vi.advanceTimersByTimeAsync(ms);

const invalidated = (client: QueryClient, queryKey: QueryKey) =>
  client.getQueryState(queryKey)?.isInvalidated;

beforeEach(() => {
  vi.useFakeTimers();
  // Half of every random wait: 500 ms of the spread.
  vi.spyOn(Math, "random").mockReturnValue(0.5);
});
afterEach(() => {
  for (const end of ended.splice(0)) end();
  vi.useRealTimers();
});

describe("broadcast.changed", () => {
  test("after the spread, reads the live stream of that market, the list of the live ones and the admin's list", async () => {
    const { client, rules } = setUp();
    const broadcast = onScreen(client, THE_BROADCAST);
    const another = onScreen(client, ANOTHER_BROADCAST);
    const live = onScreen(client, LIVE_ONES);
    const admins = onScreen(client, ADMINS_LIST);

    rules.applyLiveEvent(changed());
    await pass(SPREAD_MS / 2 - 1);

    expect(broadcast).not.toHaveBeenCalled();
    expect(live).not.toHaveBeenCalled();
    expect(admins).not.toHaveBeenCalled();
    // Nothing is written at once: the event carries the market and nothing of a live stream.
    expect(client.getQueryData(THE_BROADCAST)).toBe("held");

    await pass(1);

    expect(broadcast).toHaveBeenCalledTimes(1);
    expect(live).toHaveBeenCalledTimes(1);
    expect(admins).toHaveBeenCalledTimes(1);
    expect(client.getQueryData(THE_BROADCAST)).toBe("read again");

    await pass(60_000);

    expect(broadcast).toHaveBeenCalledTimes(1);
    expect(live).toHaveBeenCalledTimes(1);
    expect(admins).toHaveBeenCalledTimes(1);
    // The live stream of another market is not what changed.
    expect(another).not.toHaveBeenCalled();
    expect(invalidated(client, ANOTHER_BROADCAST)).toBe(false);
  });

  test("marks what is not on screen out of date: it is read when it is shown", async () => {
    const { client, rules } = setUp();
    for (const queryKey of [THE_BROADCAST, LIVE_ONES, ADMINS_LIST]) {
      client.setQueryData(queryKey, "held");
    }

    rules.applyLiveEvent(changed());
    await pass(SPREAD_MS / 2);

    expect(invalidated(client, THE_BROADCAST)).toBe(true);
    expect(invalidated(client, LIVE_ONES)).toBe(true);
    expect(invalidated(client, ADMINS_LIST)).toBe(true);
    expect(client.getQueryData(THE_BROADCAST)).toBe("held");
  });

  test("reads each thing once, however many arrive during the spread: the wait of the first one stands", async () => {
    const { client, rules } = setUp();
    const broadcast = onScreen(client, THE_BROADCAST);
    const another = onScreen(client, ANOTHER_BROADCAST);
    const live = onScreen(client, LIVE_ONES);

    // A start, the moment it is on air and three more, all within the wait of the first.
    for (let announced = 0; announced < 5; announced += 1) {
      rules.applyLiveEvent(changed());
      await pass(SPREAD_MS / 2 / 5 - 1);
    }
    expect(broadcast).not.toHaveBeenCalled();
    expect(live).not.toHaveBeenCalled();

    await pass(5);

    expect(broadcast).toHaveBeenCalledTimes(1);
    expect(live).toHaveBeenCalledTimes(1);
    await pass(60_000);
    expect(broadcast).toHaveBeenCalledTimes(1);
    expect(live).toHaveBeenCalledTimes(1);

    // The live streams of two markets change in one moment: each is read, and the list once.
    rules.applyLiveEvent(changed());
    rules.applyLiveEvent(changed(OTHER_MARKET_ID));
    await pass(SPREAD_MS / 2);
    expect(broadcast).toHaveBeenCalledTimes(2);
    expect(another).toHaveBeenCalledTimes(1);
    expect(live).toHaveBeenCalledTimes(2);
  });

  test("a wait that ends sooner takes the place of a later one, and the later one reads nothing more", async () => {
    const { client, rules } = setUp();
    const broadcast = onScreen(client, THE_BROADCAST);

    vi.mocked(Math.random).mockReturnValue(0.9);
    rules.applyLiveEvent(changed());
    vi.mocked(Math.random).mockReturnValue(0.1);
    rules.applyLiveEvent(changed());

    await pass(SPREAD_MS / 10 - 1);
    expect(broadcast).not.toHaveBeenCalled();
    await pass(1);
    expect(broadcast).toHaveBeenCalledTimes(1);

    await pass(SPREAD_MS);
    expect(broadcast).toHaveBeenCalledTimes(1);
  });

  test("never waits longer than the spread, and not at all when the spread is nothing", async () => {
    vi.mocked(Math.random).mockReturnValue(0.999);
    const { client, rules } = setUp();
    const broadcast = onScreen(client, THE_BROADCAST);

    rules.applyLiveEvent(changed());
    await pass(SPREAD_MS);
    expect(broadcast).toHaveBeenCalledTimes(1);

    const prompt = setUp({ spreadMs: 0 });
    const atOnce = onScreen(prompt.client, THE_BROADCAST);
    prompt.rules.applyLiveEvent(changed());
    await pass(0);
    expect(atOnce).toHaveBeenCalledTimes(1);
  });

  test("an announcement that arrives while the live stream is read begins no second read beside it", async () => {
    const { client, rules } = setUp();
    const answer = later();
    const broadcast = onScreen(client, THE_BROADCAST);
    broadcast.mockReturnValue(answer.promise);

    rules.applyLiveEvent(changed());
    await pass(SPREAD_MS / 2);
    expect(broadcast).toHaveBeenCalledTimes(1);
    rules.applyLiveEvent(changed());
    await pass(SPREAD_MS / 2);

    // The read that is on its way answers it.
    expect(broadcast).toHaveBeenCalledTimes(1);
    answer.answer("on air");
    await pass(60_000);
    expect(broadcast).toHaveBeenCalledTimes(1);
    expect(client.getQueryData(THE_BROADCAST)).toBe("on air");
  });
});

describe("ready", () => {
  test("reads at once what each followed topic covers", async () => {
    const { client, rules } = setUp({
      followed: () => [liveTopic.broadcast(MARKET_ID), liveTopic.markets()],
    });
    const broadcast = onScreen(client, THE_BROADCAST);
    const another = onScreen(client, ANOTHER_BROADCAST);
    const live = onScreen(client, LIVE_ONES);
    const admins = onScreen(client, ADMINS_LIST);

    rules.applyLiveEvent(readyEvent());
    await pass(0);

    expect(broadcast).toHaveBeenCalledTimes(1);
    expect(live).toHaveBeenCalledTimes(1);
    expect(admins).toHaveBeenCalledTimes(1);

    await pass(60_000);

    expect(broadcast).toHaveBeenCalledTimes(1);
    expect(live).toHaveBeenCalledTimes(1);
    expect(admins).toHaveBeenCalledTimes(1);
    // A market whose live stream the tab does not follow.
    expect(another).not.toHaveBeenCalled();
  });

  test("the topic of a market's live stream covers that live stream alone, and `markets` the two lists", async () => {
    const one = setUp({ followed: () => [liveTopic.broadcast(MARKET_ID)] });
    const broadcast = onScreen(one.client, THE_BROADCAST);
    const live = onScreen(one.client, LIVE_ONES);
    const admins = onScreen(one.client, ADMINS_LIST);

    one.rules.applyLiveEvent(readyEvent());
    await pass(60_000);

    expect(broadcast).toHaveBeenCalledTimes(1);
    expect(live).not.toHaveBeenCalled();
    expect(admins).not.toHaveBeenCalled();

    const lists = setUp({ followed: () => [liveTopic.markets()] });
    const unfollowed = onScreen(lists.client, THE_BROADCAST);
    const liveOnes = onScreen(lists.client, LIVE_ONES);
    const adminsList = onScreen(lists.client, ADMINS_LIST);

    lists.rules.applyLiveEvent(readyEvent());
    await pass(60_000);

    expect(unfollowed).not.toHaveBeenCalled();
    expect(liveOnes).toHaveBeenCalledTimes(1);
    expect(adminsList).toHaveBeenCalledTimes(1);
  });

  test("reads nothing for the topic of a market or of the ranking, and nothing while no topic is followed", async () => {
    const followed = vi.fn(() => [liveTopic.market(MARKET_ID), liveTopic.leaderboard()]);
    const { client, rules } = setUp({ followed });
    const reads = [THE_BROADCAST, LIVE_ONES, ADMINS_LIST, ...NOT_THE_RULES].map((queryKey) =>
      onScreen(client, queryKey),
    );

    rules.applyLiveEvent(readyEvent());
    followed.mockReturnValue([]);
    rules.applyLiveEvent(readyEvent());
    await pass(60_000);

    expect(followed).toHaveBeenCalledTimes(2);
    for (const read of reads) expect(read).not.toHaveBeenCalled();
  });

  test("begins a read again that was on its way: it may lack what was pushed before the stream opened", async () => {
    const { client, rules } = setUp({ followed: () => [liveTopic.broadcast(MARKET_ID)] });
    const early = later();
    const broadcast = onScreen(client, THE_BROADCAST);
    broadcast.mockReturnValueOnce(early.promise);
    rules.refreshTopic(liveTopic.broadcast(MARKET_ID), { restart: false });
    await pass(0);
    expect(broadcast).toHaveBeenCalledTimes(1);

    broadcast.mockResolvedValue("on air");
    rules.applyLiveEvent(readyEvent());
    await pass(0);

    expect(broadcast).toHaveBeenCalledTimes(2);
    expect(client.getQueryData(THE_BROADCAST)).toBe("on air");

    // What the read before the stream answers arrives too late to be shown.
    early.answer("not started");
    await pass(60_000);
    expect(client.getQueryData(THE_BROADCAST)).toBe("on air");
    expect(broadcast).toHaveBeenCalledTimes(2);
  });

  test("lets a first read that is on its way answer, and reads its query once more", async () => {
    const { client, rules } = setUp({ followed: () => [liveTopic.broadcast(MARKET_ID)] });
    const first = later();
    const broadcast = vi.fn((): Promise<string> => first.promise);
    const held = onScreen(client, LIVE_ONES);
    // The section has just been mounted: its read is out and has nothing to show yet.
    ended.push(
      new QueryObserver(client, { queryKey: THE_BROADCAST, queryFn: broadcast }).subscribe(
        () => {},
      ),
    );
    await pass(0);
    expect(broadcast).toHaveBeenCalledTimes(1);

    rules.applyLiveEvent(readyEvent());
    await pass(0);
    expect(broadcast).toHaveBeenCalledTimes(1);

    broadcast.mockResolvedValue("on air");
    first.answer("not started");
    await pass(0);

    // The read that began after the stream opened is the one that closes the gap.
    expect(broadcast).toHaveBeenCalledTimes(2);
    expect(client.getQueryData(THE_BROADCAST)).toBe("on air");
    await pass(60_000);
    expect(broadcast).toHaveBeenCalledTimes(2);
    // A topic that is not followed is not read for it.
    expect(held).not.toHaveBeenCalled();
  });

  test("answers an announcement that still waited: the read is not made a second time", async () => {
    const { client, rules } = setUp({ followed: () => [liveTopic.broadcast(MARKET_ID)] });
    const broadcast = onScreen(client, THE_BROADCAST);

    rules.applyLiveEvent(changed());
    rules.applyLiveEvent(readyEvent());
    await pass(0);
    expect(broadcast).toHaveBeenCalledTimes(1);

    await pass(60_000);
    expect(broadcast).toHaveBeenCalledTimes(1);
  });
});

describe("a poll", () => {
  test("reads what a topic covers, and lets a read that is on its way answer", async () => {
    const { client, rules } = setUp();
    const answer = later();
    const broadcast = onScreen(client, THE_BROADCAST);
    const live = onScreen(client, LIVE_ONES);
    const admins = onScreen(client, ADMINS_LIST);
    broadcast.mockReturnValue(answer.promise);

    rules.refreshTopic(liveTopic.broadcast(MARKET_ID), { restart: false });
    rules.refreshTopic(liveTopic.markets(), { restart: false });
    await pass(0);
    expect(broadcast).toHaveBeenCalledTimes(1);
    expect(live).toHaveBeenCalledTimes(1);
    expect(admins).toHaveBeenCalledTimes(1);

    // The next poll finds the read of the live stream still out: a slow connection is not asked twice.
    rules.refreshTopic(liveTopic.broadcast(MARKET_ID), { restart: false });
    await pass(0);
    expect(broadcast).toHaveBeenCalledTimes(1);

    answer.answer("on air");
    await pass(60_000);
    expect(broadcast).toHaveBeenCalledTimes(1);
    expect(client.getQueryData(THE_BROADCAST)).toBe("on air");
  });

  test("reads nothing for the topic of a market or of the ranking", async () => {
    const { client, rules } = setUp();
    const reads = [THE_BROADCAST, LIVE_ONES, ADMINS_LIST, ...NOT_THE_RULES].map((queryKey) =>
      onScreen(client, queryKey),
    );

    rules.refreshTopic(liveTopic.market(MARKET_ID), { restart: false });
    rules.refreshTopic(liveTopic.leaderboard(), { restart: false });
    await pass(60_000);

    for (const read of reads) expect(read).not.toHaveBeenCalled();
  });
});

describe("what the rules leave alone", () => {
  const OTHERS: readonly (readonly [string, LiveEvent])[] = [
    ["chat.message", CHAT_MESSAGE],
    ["chat.removed", CHAT_REMOVED],
    ["market.quote", marketQuoteEvent()],
    ["market.status", marketStatusEvent()],
    ["leaderboard.changed", leaderboardChangedEvent()],
    ["me.changed", meChangedEvent()],
    ["beat", beatEvent()],
  ];

  test.each(OTHERS)("%s does nothing to the cache", async (_name, event) => {
    const { client, rules } = setUp({
      followed: () => [
        liveTopic.broadcast(MARKET_ID),
        liveTopic.market(MARKET_ID),
        liveTopic.markets(),
        liveTopic.leaderboard(),
      ],
    });
    const everything = [THE_BROADCAST, LIVE_ONES, ADMINS_LIST, ...NOT_THE_RULES];
    const reads = everything.map((queryKey) => onScreen(client, queryKey));

    rules.applyLiveEvent(event);
    await pass(60_000);

    for (const read of reads) expect(read).not.toHaveBeenCalled();
    for (const queryKey of everything) {
      expect(invalidated(client, queryKey)).toBe(false);
      expect(client.getQueryData(queryKey)).toBe("held");
    }
    expect(vi.getTimerCount()).toBe(0);
  });

  test("no price, no ranking, nothing of the caller's own, no chat and no open quote is touched: not by an event, not by ready, not by a poll", async () => {
    const { client, rules } = setUp({
      followed: () => [
        liveTopic.broadcast(MARKET_ID),
        liveTopic.market(MARKET_ID),
        liveTopic.markets(),
        liveTopic.leaderboard(),
      ],
    });
    onScreen(client, THE_BROADCAST);
    const reads = NOT_THE_RULES.map((queryKey) => onScreen(client, queryKey));

    rules.applyLiveEvent(changed());
    rules.applyLiveEvent(readyEvent());
    for (const topic of [
      liveTopic.broadcast(MARKET_ID),
      liveTopic.market(MARKET_ID),
      liveTopic.markets(),
      liveTopic.leaderboard(),
    ]) {
      rules.refreshTopic(topic, { restart: false });
    }
    await pass(60_000);

    for (const read of reads) expect(read).not.toHaveBeenCalled();
    for (const queryKey of NOT_THE_RULES) {
      expect(invalidated(client, queryKey)).toBe(false);
      expect(client.getQueryData(queryKey)).toBe("held");
    }
  });
});

describe("stop", () => {
  test("forgets the reads that still waited, and the rules work when they are used again", async () => {
    const { client, rules } = setUp();
    const broadcast = onScreen(client, THE_BROADCAST);
    const live = onScreen(client, LIVE_ONES);

    rules.applyLiveEvent(changed());
    rules.stop();
    await pass(60_000);

    expect(broadcast).not.toHaveBeenCalled();
    expect(live).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);

    rules.applyLiveEvent(changed());
    await pass(SPREAD_MS / 2);
    expect(broadcast).toHaveBeenCalledTimes(1);
    expect(live).toHaveBeenCalledTimes(1);
  });

  test("a first read that answers after the stop is not read a second time", async () => {
    const { client, rules } = setUp({ followed: () => [liveTopic.broadcast(MARKET_ID)] });
    const first = later();
    const broadcast = vi.fn((): Promise<string> => first.promise);
    ended.push(
      new QueryObserver(client, { queryKey: THE_BROADCAST, queryFn: broadcast }).subscribe(
        () => {},
      ),
    );
    await pass(0);
    rules.applyLiveEvent(readyEvent());
    await pass(0);

    rules.stop();
    first.answer("not started");
    await pass(60_000);

    expect(broadcast).toHaveBeenCalledTimes(1);
    expect(client.getQueryData(THE_BROADCAST)).toBe("not started");
  });
});
