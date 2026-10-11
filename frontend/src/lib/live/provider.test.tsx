import { useQuery, useQueryClient } from "@tanstack/react-query";
import { act, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { keys } from "@/lib/api/keys";
import { liveBroadcastsQuery } from "@/lib/api/queries";
import type { LiveBroadcastListResponse } from "@/lib/api/schema";
import { installFakeEventSource } from "@/test/fake-event-source";
import { beatEvent, config, MARKET_ID, OTHER_MARKET_ID, readyEvent } from "@/test/fixtures";
import { json, mockApi, type Route } from "@/test/http";
import { renderHookWithProviders, renderWithProviders } from "@/test/render";

import { type LiveEventOf, liveTopic, useLive, useLiveEvent, useLiveTopic } from "./index";
import type { LiveTopic } from "./topics";

const LIVE: Route = "GET /api/broadcasts/live";

const BROADCAST_ID = "5b0c7a52-3d1e-4f8a-9c60-2e7d4b1a8f33";

/** `broadcast.changed`: something about the live stream of a market changed. */
const changed = (
  overrides: Partial<LiveEventOf<"broadcast.changed">> = {},
): LiveEventOf<"broadcast.changed"> => ({
  type: "broadcast.changed",
  market_id: MARKET_ID,
  ts: "2026-10-04T10:00:00Z",
  ...overrides,
});

/** `chat.message`: one line of the chat of a live stream. */
const chatMessage = (text: string): LiveEventOf<"chat.message"> => ({
  type: "chat.message",
  market_id: MARKET_ID,
  broadcast_id: BROADCAST_ID,
  message: {
    id: "c1d2e3f4-0a1b-4c2d-8e3f-4a5b6c7d8e9f",
    username: "anna_m",
    text,
    from_vis: false,
    role: "USER",
  },
  ts: "2026-10-04T10:00:01Z",
});

const CHAT_REMOVED: LiveEventOf<"chat.removed"> = {
  type: "chat.removed",
  market_id: MARKET_ID,
  broadcast_id: BROADCAST_ID,
  message_id: "c1d2e3f4-0a1b-4c2d-8e3f-4a5b6c7d8e9f",
  ts: "2026-10-04T10:00:02Z",
};

const NOTHING_ON_AIR: LiveBroadcastListResponse = { items: [] };
const ON_AIR: LiveBroadcastListResponse = {
  items: [
    {
      broadcast_id: BROADCAST_ID,
      market_id: MARKET_ID,
      question: "Will the keynote start on time?",
      started_at: "2026-10-04T10:00:00Z",
    },
  ],
};

/** A component in miniature: it names a topic and says whether the stream is open. */
function Follower({ topic, refresh }: { topic: LiveTopic | null; refresh?: () => void }) {
  useLiveTopic(topic, { refresh });
  const { live } = useLive();
  return <p>{live ? "live" : "not live"}</p>;
}

/** "Live now" in miniature: it names `markets` and shows the live streams that are on air. */
function LiveNow() {
  useLiveTopic(liveTopic.markets());
  const live = useQuery(liveBroadcastsQuery);
  if (live.data === undefined) return <p>loading</p>;
  const questions = live.data.items.map((item) => item.question);
  return <p>on air: {questions.length === 0 ? "nothing" : questions.join(", ")}</p>;
}

describe("the live hooks", () => {
  let streams: ReturnType<typeof installFakeEventSource>;

  beforeEach(() => {
    streams = installFakeEventSource();
    // The first poll after a loss comes late in its interval: no test here waits for it
    // unless it sets another part.
    vi.spyOn(Math, "random").mockReturnValue(0.99);
  });

  describe("outside a provider", () => {
    test("do nothing, and live is false: no stream is opened and nothing is heard", () => {
      const refresh = vi.fn();
      const heard = vi.fn();

      const { result } = renderHookWithProviders(() => {
        useLiveTopic(liveTopic.markets(), { refresh });
        useLiveEvent("ready", heard);
        return useLive();
      });

      expect(result.current).toEqual({ live: false });
      expect(streams.instances).toHaveLength(0);
      expect(refresh).not.toHaveBeenCalled();
      expect(heard).not.toHaveBeenCalled();
    });
  });

  describe("useLiveTopic", () => {
    test("the page's one stream names what the mounted components name together, from their mount on", () => {
      renderWithProviders(
        <>
          <Follower topic={liveTopic.markets()} />
          <Follower topic={liveTopic.broadcast(MARKET_ID)} />
          <Follower topic={liveTopic.market(MARKET_ID)} />
          <Follower topic={liveTopic.markets()} />
          <Follower topic={null} />
        </>,
        { live: true },
      );

      expect(streams.instances).toHaveLength(1);
      expect(streams.latest().topics).toEqual([
        `broadcast:${MARKET_ID}`,
        `market:${MARKET_ID}`,
        "markets",
      ]);
      expect(streams.latest().tab).toMatch(/^[A-Za-z0-9_-]{8,36}$/);
    });

    test("a stream is held while no component names a topic", () => {
      renderWithProviders(<p>a screen that names none</p>, { live: true });

      expect(streams.held()).toHaveLength(1);
      expect(streams.latest().topics).toEqual([]);
    });

    test("a component that goes and one that comes exchange the stream once", async () => {
      const { rerender } = renderWithProviders(<Follower topic={liveTopic.markets()} />, {
        live: true,
      });
      const first = streams.latest();
      act(() => first.send(readyEvent()));

      rerender(<Follower topic={liveTopic.broadcast(MARKET_ID)} />);

      await waitFor(() => expect(streams.instances).toHaveLength(2));
      expect(first.closed).toBe(true);
      expect(streams.latest().topics).toEqual([`broadcast:${MARKET_ID}`]);
      expect(streams.latest().tab).toBe(first.tab);
      // The stream is exchanged, not lost.
      expect(screen.getByText("live")).toBeInTheDocument();
      await act(() => new Promise((resolve) => setTimeout(resolve, 120)));
      expect(streams.instances).toHaveLength(2);
    });

    test("calls refresh after every ready and at every poll, and not for an event", async () => {
      vi.mocked(Math.random).mockReturnValue(0);
      const refresh = vi.fn();
      const other = vi.fn();
      renderWithProviders(
        <>
          <Follower topic={liveTopic.market(MARKET_ID)} refresh={refresh} />
          <Follower topic={liveTopic.market(MARKET_ID)} refresh={other} />
        </>,
        { live: true },
      );
      expect(refresh).not.toHaveBeenCalled();

      act(() => streams.latest().send(readyEvent()));
      expect(refresh).toHaveBeenCalledTimes(1);
      expect(other).toHaveBeenCalledTimes(1);

      act(() => streams.latest().send(chatMessage("Hello")));
      act(() => streams.latest().send(changed()));
      act(() => streams.latest().send(beatEvent()));
      expect(refresh).toHaveBeenCalledTimes(1);

      // The stream is lost: the first poll comes at once here.
      act(() => streams.latest().fail());
      await waitFor(() => expect(refresh).toHaveBeenCalledTimes(2));
      expect(other).toHaveBeenCalledTimes(2);

      act(() => streams.latest().send(readyEvent()));
      expect(refresh).toHaveBeenCalledTimes(3);
    });

    test("calls the refresh of the latest render, and none once its component is gone", () => {
      const first = vi.fn();
      const second = vi.fn();
      const { rerender } = renderWithProviders(
        <Follower topic={liveTopic.markets()} refresh={first} />,
        { live: true },
      );
      rerender(<Follower topic={liveTopic.markets()} refresh={second} />);

      act(() => streams.latest().send(readyEvent()));

      expect(first).not.toHaveBeenCalled();
      expect(second).toHaveBeenCalledTimes(1);
      // The topic stayed: nothing was exchanged for the other function.
      expect(streams.instances).toHaveLength(1);

      rerender(<p>another screen</p>);
      act(() => streams.latest().send(readyEvent()));

      expect(second).toHaveBeenCalledTimes(1);
    });
  });

  describe("useLive", () => {
    test("is true from ready until a loss", () => {
      renderWithProviders(<Follower topic={liveTopic.markets()} />, { live: true });
      expect(screen.getByText("not live")).toBeInTheDocument();

      act(() => streams.latest().open());
      expect(screen.getByText("not live")).toBeInTheDocument();

      act(() => streams.latest().send(readyEvent()));
      expect(screen.getByText("live")).toBeInTheDocument();

      act(() => streams.latest().fail());
      expect(screen.getByText("not live")).toBeInTheDocument();
    });

    test("closes the stream when the provider goes", () => {
      const { unmount } = renderWithProviders(<Follower topic={liveTopic.markets()} />, {
        live: true,
      });
      expect(streams.held()).toHaveLength(1);

      unmount();

      expect(streams.held()).toHaveLength(0);
    });
  });

  describe("in React's strict mode, which mounts everything twice in development", () => {
    test("holds one stream with its topics, and tells each follower once", async () => {
      const refresh = vi.fn();
      const heard = vi.fn();
      function Screen() {
        useLiveEvent("broadcast.changed", heard);
        return <Follower topic={liveTopic.broadcast(MARKET_ID)} refresh={refresh} />;
      }

      renderWithProviders(<Screen />, { live: true, reactStrictMode: true });

      // The stream of the first mount was closed before the second one opened.
      expect(streams.instances).toHaveLength(2);
      expect(streams.instances[0]!.closed).toBe(true);
      expect(streams.held()).toHaveLength(1);
      expect(streams.latest().topics).toEqual([`broadcast:${MARKET_ID}`]);
      expect(new Set(streams.instances.map((stream) => stream.tab)).size).toBe(1);

      act(() => streams.latest().send(readyEvent()));
      act(() => streams.latest().send(changed()));

      expect(screen.getByText("live")).toBeInTheDocument();
      expect(refresh).toHaveBeenCalledTimes(1);
      expect(heard).toHaveBeenCalledTimes(1);
      // Nothing of the first mount is left to exchange the stream afterwards.
      await act(() => new Promise((resolve) => setTimeout(resolve, 120)));
      expect(streams.held()).toHaveLength(1);
      expect(streams.latest().closed).toBe(false);
    });
  });

  describe("useLiveEvent", () => {
    test("hears every event of its name with its body, whichever topic brought it, and no other", () => {
      const messages = vi.fn();
      const removals = vi.fn();
      const changes = vi.fn();
      renderHookWithProviders(
        () => {
          useLiveTopic(liveTopic.market(MARKET_ID));
          useLiveEvent("chat.message", messages);
          useLiveEvent("chat.removed", removals);
          useLiveEvent("broadcast.changed", changes);
        },
        { live: true },
      );

      act(() => streams.latest().send(readyEvent()));
      act(() => streams.latest().send(chatMessage("Hello")));
      act(() => streams.latest().send(chatMessage("Is it on?")));
      act(() => streams.latest().send(CHAT_REMOVED));
      act(() => streams.latest().send(changed({ market_id: OTHER_MARKET_ID })));

      expect(messages.mock.calls).toEqual([[chatMessage("Hello")], [chatMessage("Is it on?")]]);
      expect(removals.mock.calls).toEqual([[CHAT_REMOVED]]);
      expect(changes.mock.calls).toEqual([[changed({ market_id: OTHER_MARKET_ID })]]);
    });

    test("hears every ready", async () => {
      const ready = vi.fn();
      const { rerender } = renderHookWithProviders(
        ({ topic }: { topic: LiveTopic }) => {
          useLiveTopic(topic);
          useLiveEvent("ready", ready);
        },
        { live: true, initialProps: { topic: liveTopic.markets() } },
      );

      act(() => streams.latest().send(readyEvent()));
      rerender({ topic: liveTopic.broadcast(MARKET_ID) });
      await waitFor(() => expect(streams.instances).toHaveLength(2));
      act(() => streams.latest().send(readyEvent({ ts: "2026-10-04T10:02:00Z" })));

      expect(ready.mock.calls).toEqual([
        [readyEvent()],
        [readyEvent({ ts: "2026-10-04T10:02:00Z" })],
      ]);
    });

    test("hears an event after the rules have taken it: at a ready the read of what its topics cover is out", async () => {
      const api = mockApi({ [LIVE]: json(200, NOTHING_ON_AIR) });
      const seen: number[] = [];
      function Screen() {
        const queryClient = useQueryClient();
        useLiveEvent("ready", () => {
          seen.push(queryClient.isFetching({ queryKey: keys.broadcasts.live }));
        });
        return <LiveNow />;
      }
      renderWithProviders(<Screen />, { live: true });
      await screen.findByText("on air: nothing");

      act(() => streams.latest().send(readyEvent()));

      expect(seen).toEqual([1]);
      await waitFor(() => expect(api.calls(LIVE)).toHaveLength(2));
    });

    test("calls the handler of the latest render", () => {
      const first = vi.fn();
      const second = vi.fn();
      const { rerender } = renderHookWithProviders(
        ({ handler }: { handler: () => void }) => useLiveEvent("broadcast.changed", handler),
        { live: true, initialProps: { handler: first } },
      );
      rerender({ handler: second });

      act(() => streams.latest().send(changed()));

      expect(first).not.toHaveBeenCalled();
      expect(second).toHaveBeenCalledTimes(1);
    });
  });

  describe("what the stream does to a component", () => {
    test("whenever a stream opens, the tab reads once what its topics cover, and nothing else", async () => {
      const api = mockApi({ [LIVE]: json(200, NOTHING_ON_AIR) });
      renderWithProviders(<LiveNow />, { live: true });
      await screen.findByText("on air: nothing");
      expect(api.calls(LIVE)).toHaveLength(1);

      // What was pushed before the stream opened is not sent again: the read brings it.
      api.on(LIVE, json(200, ON_AIR));
      act(() => streams.latest().send(readyEvent()));

      expect(
        await screen.findByText("on air: Will the keynote start on time?"),
      ).toBeInTheDocument();
      expect(api.calls(LIVE)).toHaveLength(2);
      // `/me` is read as the app reads it without the stream: a stream that opens asks for none.
      expect(api.requests.map((request) => request.path)).toEqual([
        "/api/broadcasts/live",
        "/api/broadcasts/live",
      ]);
    });

    test("an announcement is read after the spread: broadcast.changed brings the live stream that started", async () => {
      const api = mockApi({ [LIVE]: json(200, NOTHING_ON_AIR) });
      renderWithProviders(<LiveNow />, {
        live: true,
        config: config({ live_announce_spread_ms: 0 }),
      });
      await screen.findByText("on air: nothing");
      act(() => streams.latest().send(readyEvent()));
      await waitFor(() => expect(api.calls(LIVE)).toHaveLength(2));

      api.on(LIVE, json(200, ON_AIR));
      act(() => streams.latest().send(changed()));

      expect(
        await screen.findByText("on air: Will the keynote start on time?"),
      ).toBeInTheDocument();
      expect(api.calls(LIVE)).toHaveLength(3);

      // Its end takes it away again.
      api.on(LIVE, json(200, NOTHING_ON_AIR));
      act(() => streams.latest().send(changed({ ts: "2026-10-04T10:30:00Z" })));

      expect(await screen.findByText("on air: nothing")).toBeInTheDocument();
      expect(api.calls(LIVE)).toHaveLength(4);
    });

    test("without a stream the tab polls what its topics cover, and nothing on screen says so", async () => {
      vi.mocked(Math.random).mockReturnValue(0);
      const api = mockApi({ [LIVE]: json(200, NOTHING_ON_AIR) });
      renderWithProviders(<LiveNow />, { live: true });
      await screen.findByText("on air: nothing");

      api.on(LIVE, json(200, ON_AIR));
      act(() => streams.latest().fail());

      expect(
        await screen.findByText("on air: Will the keynote start on time?"),
      ).toBeInTheDocument();
      expect(api.calls(LIVE)).toHaveLength(2);
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(screen.queryByRole("status")).not.toBeInTheDocument();
      // The poll tried the stream again.
      expect(streams.instances).toHaveLength(2);
    });

    test("a browser without EventSource shows the component and polls", async () => {
      vi.stubGlobal("EventSource", undefined);
      vi.mocked(Math.random).mockReturnValue(0);
      const api = mockApi({ [LIVE]: json(200, NOTHING_ON_AIR) });

      renderWithProviders(<LiveNow />, { live: true });

      expect(await screen.findByText("on air: nothing")).toBeInTheDocument();
      await waitFor(() => expect(api.calls(LIVE)).toHaveLength(2));
    });
  });
});
