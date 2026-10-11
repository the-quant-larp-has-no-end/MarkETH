"use client";

import { type QueryClient, useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  type ReactNode,
  useContext,
  useEffect,
  useEffectEvent,
  useMemo,
  useSyncExternalStore,
} from "react";

import { useConfig } from "../api/session";
import { LiveClient } from "./client";
import type { LiveEvent, LiveEventName, LiveEventOf } from "./events";
import { createLiveRules } from "./rules";
import type { LiveTopic } from "./topics";

/** What the paces of `/config` say about the stream, as the provider takes them. */
type LivePaces = {
  heartbeatSeconds: number;
  pollSeconds: number;
  spreadMs: number;
};

/** The page's stream and what the mounted components asked of it. */
type Live = {
  client: LiveClient;
  start(): void;
  stop(): void;
  /** Wants a topic, and calls `refresh` after every `ready` and at every poll, until released. */
  follow(topic: LiveTopic, refresh: () => void): () => void;
  /** Calls `handler` for every event of one name, until released. */
  hear(name: LiveEventName, handler: (event: LiveEvent) => void): () => void;
};

function createLive(queryClient: QueryClient, paces: LivePaces): Live {
  const refreshers = new Map<LiveTopic, Set<() => void>>();
  const hearers = new Map<LiveEventName, Set<(event: LiveEvent) => void>>();

  /** What each component that follows a topic reads itself. */
  const refreshFollowers = () => {
    for (const topic of client.topics) {
      for (const refresh of [...(refreshers.get(topic) ?? [])]) refresh();
    }
  };

  const client: LiveClient = new LiveClient({
    heartbeatSeconds: paces.heartbeatSeconds,
    pollSeconds: paces.pollSeconds,
    onEvent(event) {
      rules.applyLiveEvent(event);
      // No event is sent a second time: whatever a component keeps beside the cache is
      // read once whenever a stream opens.
      if (event.type === "ready") refreshFollowers();
      for (const hear of [...(hearers.get(event.type) ?? [])]) hear(event);
    },
    onPoll() {
      for (const topic of client.topics) rules.refreshTopic(topic, { restart: false });
      refreshFollowers();
    },
  });
  const rules = createLiveRules(queryClient, {
    spreadMs: paces.spreadMs,
    followed: () => client.topics,
  });

  /** Keeps `entry` in the set of `key` until the returned function is called. */
  const keep = <Key, Entry>(sets: Map<Key, Set<Entry>>, key: Key, entry: Entry) => {
    let set = sets.get(key);
    if (set === undefined) {
      set = new Set();
      sets.set(key, set);
    }
    set.add(entry);
    return () => {
      set.delete(entry);
      // Only the set that is in place is taken away: a release may be called twice.
      if (set.size === 0 && sets.get(key) === set) sets.delete(key);
    };
  };

  return {
    client,
    start: () => client.start(),
    stop() {
      client.stop();
      rules.stop();
    },
    follow(topic, refresh) {
      const forget = keep(refreshers, topic, refresh);
      const release = client.follow(topic);
      return () => {
        forget();
        release();
      };
    },
    hear: (name, handler) => keep(hearers, name, handler),
  };
}

const LiveContext = createContext<Live | null>(null);

/**
 * The app's one event stream (DESIGN.md §14, "Live data"). It is mounted once, around
 * everything that has a profile, and holds the page's `LiveClient` for as long as it
 * stands: one stream per visible tab, whose topics are what the mounted components name
 * with `useLiveTopic`. The components of a live stream and of its chat follow it; what an
 * event does to the query cache is `lib/live/rules.ts`.
 *
 * Nothing it does is seen: a stream that cannot be held costs speed, and no error.
 * Outside a provider the three hooks do nothing and `live` is false.
 */
export function LiveProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const {
    live_heartbeat_seconds: heartbeatSeconds,
    live_poll_fallback_seconds: pollSeconds,
    live_announce_spread_ms: spreadMs,
  } = useConfig();
  const live = useMemo(
    () => createLive(queryClient, { heartbeatSeconds, pollSeconds, spreadMs }),
    [queryClient, heartbeatSeconds, pollSeconds, spreadMs],
  );

  useEffect(() => {
    live.start();
    return () => live.stop();
  }, [live]);

  return <LiveContext.Provider value={live}>{children}</LiveContext.Provider>;
}

const subscribeToNothing = () => () => {};

/**
 * Whether the stream is open: true from `ready` until a loss. It is for deciding what to
 * read, never for showing: nothing on screen says whether the stream is open.
 */
export function useLive(): { live: boolean } {
  const context = useContext(LiveContext);
  const live = useSyncExternalStore(
    context?.client.subscribe ?? subscribeToNothing,
    () => context?.client.live ?? false,
    () => false,
  );
  return useMemo(() => ({ live }), [live]);
}

/**
 * Says which topic a component shows, from its mount on: the stream follows what the
 * mounted components name together. `null` names none.
 *
 *     useLiveTopic(liveTopic.market(marketId), { refresh: readTheChat });
 *
 * `refresh` is for what a component keeps beside the rules of `lib/live/rules.ts`: it is
 * called after every `ready` and at every poll, which is when the tab reads what its
 * topics cover.
 */
export function useLiveTopic(topic: LiveTopic | null, options?: { refresh?: () => void }): void {
  const context = useContext(LiveContext);
  const refresh = useEffectEvent(() => options?.refresh?.());

  useEffect(() => {
    if (context === null || topic === null) return;
    return context.follow(topic, () => refresh());
  }, [context, topic]);
}

/**
 * Hears every event of one name, whichever topic brought it, after the cache has taken
 * it: a chat message that arrives, a live stream that changed.
 * `useLiveEvent("ready", …)` hears every `ready`.
 */
export function useLiveEvent<Name extends LiveEventName>(
  name: Name,
  handler: (event: LiveEventOf<Name>) => void,
): void {
  const context = useContext(LiveContext);
  const heard = useEffectEvent(handler);

  useEffect(() => {
    if (context === null) return;
    return context.hear(name, (event) => heard(event as LiveEventOf<Name>));
  }, [context, name]);
}
