import type { QueryClient, QueryKey } from "@tanstack/react-query";

import { keys } from "../api/keys";
import type { LiveEvent } from "./events";
import { broadcastMarketOfTopic, type LiveTopic } from "./topics";

export type LiveRulesOptions = {
  /** `live_announce_spread_ms` of `/config`: the longest an announcement waits for its read. */
  spreadMs: number;
  /** The topics the tab follows now. */
  followed: () => readonly LiveTopic[];
};

/** How a read treats a read of the same thing that is on its way. */
export type ReadOptions = {
  /**
   * True begins such a read again: what it asked before this moment may lack what was
   * pushed since. False lets it answer, so that a slow connection is not asked twice.
   */
  restart: boolean;
};

export type LiveRules = {
  /** What one event does to the cache: the table below. */
  applyLiveEvent(event: LiveEvent): void;
  /** Reads again what a topic covers. */
  refreshTopic(topic: LiveTopic, options: ReadOptions): void;
  /** Forgets every read that is still waiting. */
  stop(): void;
};

/**
 * What the event stream does to the query cache (DESIGN.md §14, "Live data"): the
 * counterpart of the invalidation table of `lib/api/mutations.ts`. An event is a hint, not
 * truth: it marks out of date, and what is on screen is read again. Nothing here writes a
 * value, and nothing here works a figure out.
 *
 * | Event               | Written at once | Asked again                                    |
 * | ------------------- | --------------- | ---------------------------------------------- |
 * | `ready`             | nothing         | at once: what each followed topic covers       |
 * | `broadcast.changed` | nothing         | the live stream of that market; the list of    |
 * |                     |                 | the live ones; the admin's list of live        |
 * |                     |                 | streams                                        |
 * | `chat.message`,     | nothing         | nothing: the chat is its panel's own           |
 * | `chat.removed`      |                 | (`features/chat`). The panel hears both, and   |
 * |                     |                 | reads its chat at a `ready`, at a poll and at  |
 * |                     |                 | a `broadcast.changed` of its market            |
 * | every other event   | nothing         | nothing                                        |
 *
 * A topic covers: `markets`, the list of the live streams and the admin's list of them;
 * `broadcast:{id}`, the live stream of that market. `market:{id}` and `leaderboard` cover
 * nothing here: a component that follows one reads what it shows through the `refresh`
 * of `useLiveTopic`.
 *
 * **The spread.** An announcement is answered after a random wait of up to
 * `live_announce_spread_ms`, and the read that waits answers every announcement of the
 * same thing that arrives meanwhile, as a read that is on its way does. The people who
 * watch one live stream therefore do not all ask in the same instant. `ready` does not
 * wait: no event is sent a second time, so the read that follows it is what closes the
 * gap, and it begins after the stream opened.
 *
 * No price, no ranking and nothing of the caller's own is touched: nothing here names a
 * market's detail, its history, its quotes, a list of markets, the leaderboard or `/me`.
 * Those are read as their screens read them.
 */
export function createLiveRules(client: QueryClient, options: LiveRulesOptions): LiveRules {
  /** The reads that wait out their spread, by what they read. */
  const waiting = new Map<string, { timer: number; due: number }>();
  /** Counts the stops: an answer that arrives after one belongs to nobody. */
  let stops = 0;

  const thingOf = (queryKey: QueryKey) => JSON.stringify(queryKey);

  /**
   * Marks out of date; what is on screen is read again.
   *
   * `restart` asks for a read that begins now. A read that is on its way is begun again,
   * except the first read of a query, which has nothing to show meanwhile: that one is
   * let to answer, and its query is read once more.
   */
  const read = (queryKey: QueryKey, { exact = false, restart = false } = {}): Promise<void> => {
    if (restart) forget(queryKey);
    const begunIn = stops;
    const firstReads = restart
      ? client
          .getQueryCache()
          .findAll({ queryKey, exact, fetchStatus: "fetching" })
          .filter((query) => query.state.data === undefined)
          .map((query) => query.queryHash)
      : [];
    return client.invalidateQueries({ queryKey, exact }, { cancelRefetch: restart }).then(() => {
      if (firstReads.length === 0 || begunIn !== stops) return;
      return client.invalidateQueries(
        { queryKey, exact, predicate: (query) => firstReads.includes(query.queryHash) },
        { cancelRefetch: false },
      );
    });
  };

  /** A read that begins now answers what waited for one of the same thing. */
  const forget = (queryKey: QueryKey) => {
    const thing = thingOf(queryKey);
    const pending = waiting.get(thing);
    if (pending === undefined) return;
    window.clearTimeout(pending.timer);
    waiting.delete(thing);
  };

  /** Reads after `wait` ms, unless a read of the same thing waits already and comes sooner. */
  const ask = (queryKey: QueryKey, wait: number, exact = false) => {
    const thing = thingOf(queryKey);
    const due = Date.now() + wait;
    const pending = waiting.get(thing);
    if (pending !== undefined) {
      if (pending.due <= due) return;
      window.clearTimeout(pending.timer);
    }
    const timer = window.setTimeout(() => {
      waiting.delete(thing);
      void read(queryKey, { exact });
    }, wait);
    waiting.set(thing, { timer, due });
  };

  const spread = () => Math.random() * options.spreadMs;

  const rules: LiveRules = {
    applyLiveEvent(event) {
      switch (event.type) {
        case "ready":
          for (const topic of options.followed()) rules.refreshTopic(topic, { restart: true });
          break;
        case "broadcast.changed": {
          const wait = spread();
          ask(keys.markets.broadcast(event.market_id), wait, true);
          ask(keys.broadcasts.live, wait);
          ask(keys.admin.broadcasts, wait);
          break;
        }
        default:
          break;
      }
    },

    refreshTopic(topic, { restart }) {
      if (topic === "markets") {
        void read(keys.broadcasts.live, { restart });
        void read(keys.admin.broadcasts, { restart });
        return;
      }
      const marketId = broadcastMarketOfTopic(topic);
      if (marketId !== null) void read(keys.markets.broadcast(marketId), { exact: true, restart });
    },

    stop() {
      stops += 1;
      for (const pending of waiting.values()) window.clearTimeout(pending.timer);
      waiting.clear();
    },
  };
  return rules;
}
