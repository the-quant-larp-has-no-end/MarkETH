import type { QueryClient } from "@tanstack/react-query";

import { keys } from "./keys";
import type { ChatMessageItem, ChatResponse, PublicConfigResponse } from "./schema";

/**
 * What one message does to the cached chat of its market (DESIGN.md §14, "Live data").
 *
 * A message reaches a tab twice over: as the answer of its own post and as the event
 * `chat.message`; a removal as the answer of the admin's write and as `chat.removed`. Both
 * ways put it into the cache with the two functions here, so the list is the same
 * whichever arrives first. `lib/api/mutations.ts` calls them for the answers, and whoever
 * follows the event stream calls them for the events.
 *
 * The list stands in the order to show it: a message joins at its end, nothing is sorted,
 * and ids are compared for equality alone.
 */

/**
 * Puts a message at the end of the cached chat of a market and says whether the chat
 * holds it afterwards. A message whose id is there already is not put a second time. The
 * list then keeps its latest `chat_backlog_size` messages (`GET /config`).
 *
 * `broadcastId` is the live stream an event names: a message of another live stream than
 * the cached chat's is dropped. The answer of a post names none. A chat that is not in
 * the cache, or is closed there, takes no message and the answer is false: the cache is
 * behind, and the read that brings the open chat brings its messages.
 */
export function putChatMessage(
  client: QueryClient,
  marketId: string,
  message: ChatMessageItem,
  broadcastId?: string,
): boolean {
  const key = keys.markets.chat(marketId);
  const chat = client.getQueryData<ChatResponse>(key);
  if (chat === undefined || !chat.open) return false;
  if (broadcastId !== undefined && chat.broadcast_id !== broadcastId) return false;
  if (chat.items.some((item) => item.id === message.id)) return true;

  const backlog = client.getQueryData<PublicConfigResponse>(keys.config)?.chat_backlog_size;
  const items = [...chat.items, message];
  client.setQueryData<ChatResponse>(key, {
    ...chat,
    items: backlog === undefined ? items : items.slice(-backlog),
  });
  return true;
}

/** Takes the message of this id out of the cached chat of a market, if it is there. */
export function dropChatMessage(client: QueryClient, marketId: string, messageId: string): void {
  client.setQueryData<ChatResponse>(keys.markets.chat(marketId), (chat) =>
    chat === undefined || !chat.items.some((item) => item.id === messageId)
      ? chat
      : { ...chat, items: chat.items.filter((item) => item.id !== messageId) },
  );
}
