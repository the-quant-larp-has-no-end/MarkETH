"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo } from "react";

import { chatQuery } from "@/lib/api/queries";
import { useConfig } from "@/lib/api/session";
import { liveTopic, useLiveEvent, useLiveTopic } from "@/lib/live";

import { createChatFollower } from "./chat-follower";

/**
 * The live chat of a market, read and followed (DESIGN.md §13, "Live chat"; §14, "Live
 * data"): `GET /markets/{market_id}/chat`, and what the event stream brings since.
 *
 * The chat is read when the panel mounts and when the window gets the focus back, as
 * every query is; after every `ready` and at every poll, which the topic of the market
 * brings; and after a `broadcast.changed` of the market, which says that a live stream
 * began or ended or that messages left it. `chat.message` and `chat.removed` change the
 * list at once and ask the API for nothing.
 *
 * The events of every market the tab follows arrive here: each is held against this
 * market. `broadcast.changed` travels on the topic of the market's live stream, which the
 * section "Live stream" names.
 */
export function useChat(marketId: string) {
  const client = useQueryClient();
  const { live_announce_spread_ms: spreadMs } = useConfig();
  const follower = useMemo(
    () => createChatFollower(client, marketId, { spreadMs }),
    [client, marketId, spreadMs],
  );
  useEffect(() => follower.start(), [follower]);

  const chat = useQuery(chatQuery(marketId));

  useLiveTopic(liveTopic.market(marketId), {
    refresh: () => follower.read({ restart: false }),
  });
  useLiveEvent("ready", () => follower.read({ restart: true }));
  useLiveEvent("broadcast.changed", (event) => {
    if (event.market_id === marketId) follower.announced();
  });
  useLiveEvent("chat.message", (event) => {
    if (event.market_id === marketId) follower.put(event.message, event.broadcast_id);
  });
  useLiveEvent("chat.removed", (event) => {
    if (event.market_id === marketId) follower.drop(event.message_id);
  });

  return { chat, follower };
}
