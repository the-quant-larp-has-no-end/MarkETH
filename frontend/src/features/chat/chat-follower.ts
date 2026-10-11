import { hashKey, type QueryClient } from "@tanstack/react-query";

import { dropChatMessage, putChatMessage } from "@/lib/api/chat-cache";
import { keys } from "@/lib/api/keys";
import type { ChatMessageItem } from "@/lib/api/schema";

export type ChatFollowerOptions = {
  /** `live_announce_spread_ms` of `/config`: the longest an announcement waits for its read. */
  spreadMs: number;
};

export type ChatFollower = {
  /** Follows the reads of the chat from here on. Returns the function that ends it. */
  start(): () => void;
  /**
   * A message arrived: as the event `chat.message`, which names its live stream, or as
   * the answer of the viewer's own post, which names none.
   */
  put(message: ChatMessageItem, broadcastId?: string): void;
  /** A message is removed: by the event `chat.removed`, or by the answer of a removal. */
  drop(messageId: string): void;
  /**
   * Reads the chat. `restart` asks for a read that begins now: the stream opened, and
   * nothing that was pushed before that moment is sent again. Without it a read that is
   * on its way is let to answer. Everything that asks in the same turn is one read.
   */
  read(options: { restart: boolean }): void;
  /** `broadcast.changed` of the market: the chat is read after the spread. */
  announced(): void;
};

/**
 * What keeps the cached chat of one market in step with the event stream (DESIGN.md §14,
 * "The live chat"). The list is what the latest read returned and what arrived since.
 *
 * - **A message and a removal** go into the cache through `putChatMessage` and
 *   `dropChatMessage` (`lib/api/chat-cache.ts`), which the answers of the two writes use
 *   as well: a message shows once, whichever of its answer and its event arrives first.
 * - **A read replaces the list.** What arrived while that read was on its way can be
 *   later than what the read saw, so it is kept and put again, in the order it arrived,
 *   once the read has answered. A read that begins has seen everything before it, and
 *   one that fails leaves the list as it is: both forget what was kept.
 * - **A read that follows `ready` begins after it.** A read that is on its way is begun
 *   again, except the first read of the chat, which has nothing to show meanwhile: that
 *   one is let to answer, and the chat is read once more.
 * - **An announcement is answered after a random wait** of up to `spreadMs`, by one read
 *   however many arrive during the wait: the people who watch one live stream do not all
 *   ask in the same instant.
 *
 * Nothing here orders a list or decides anything: ids are compared for equality alone.
 */
export function createChatFollower(
  client: QueryClient,
  marketId: string,
  { spreadMs }: ChatFollowerOptions,
): ChatFollower {
  const queryKey = keys.markets.chat(marketId);
  const chat = { queryKey, exact: true } as const;
  const hash = hashKey(queryKey);

  /** What arrived since the read that is on its way began, in the order it arrived. */
  let during: (() => void)[] = [];
  /** Counts the starts and the stops: what was asked before one belongs to nobody after it. */
  let run = 0;
  let following = false;
  /** The read this turn asks for, until the turn is over. */
  let asked: { restart: boolean } | null = null;
  /** The timer of an announcement that waits out its spread. */
  let announcement: number | null = null;

  const state = () => client.getQueryState(queryKey);
  const reading = () => state()?.fetchStatus === "fetching";

  const arrived = (apply: () => void) => {
    apply();
    if (reading()) during.push(apply);
  };

  const read = (restart: boolean) => {
    if (!restart) {
      void client.invalidateQueries(chat, { cancelRefetch: false });
      return;
    }
    const first = reading() && state()?.data === undefined;
    const begunIn = run;
    void client.invalidateQueries(chat, { cancelRefetch: true }).then(() => {
      // A first read that failed has brought nothing to read again: the panel offers "Try again".
      if (!first || begunIn !== run || state()?.data === undefined) return;
      return client.invalidateQueries(chat, { cancelRefetch: false });
    });
  };

  return {
    start() {
      run += 1;
      following = true;
      const unsubscribe = client.getQueryCache().subscribe((event) => {
        if (event.type !== "updated" || event.query.queryHash !== hash) return;
        const { action } = event;
        if (action.type === "fetch" || action.type === "error") {
          during = [];
        } else if (action.type === "success" && action.manual !== true) {
          const again = during;
          during = [];
          for (const apply of again) apply();
        }
      });
      return () => {
        run += 1;
        following = false;
        unsubscribe();
        if (announcement !== null) window.clearTimeout(announcement);
        announcement = null;
        asked = null;
        during = [];
      };
    },

    put(message, broadcastId) {
      arrived(() => putChatMessage(client, marketId, message, broadcastId));
    },

    drop(messageId) {
      arrived(() => dropChatMessage(client, marketId, messageId));
    },

    read({ restart }) {
      if (asked !== null) {
        asked.restart ||= restart;
        return;
      }
      const turn = { restart };
      const begunIn = run;
      asked = turn;
      queueMicrotask(() => {
        if (asked === turn) asked = null;
        if (following && begunIn === run) read(turn.restart);
      });
    },

    announced() {
      if (announcement !== null) return;
      const begunIn = run;
      announcement = window.setTimeout(() => {
        announcement = null;
        // The read that is on its way may have begun before what was announced.
        if (following && begunIn === run) read(true);
      }, Math.random() * spreadMs);
    },
  };
}
