"use client";

import { type QueryClient, useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";

import { keys } from "../api/keys";
import type {
  HistoryResponse,
  MarketDetailResponse,
  MarketStatus,
  QuoteEvent,
  StatusEvent,
} from "../api/schema";
import { useConfig } from "../api/session";
import { isZero } from "../format/amount";
import { useEventStream } from "./event-stream";

/** How often the market is asked again in a row to catch up with the stream. */
const CATCH_UP_ROUNDS = 3;

/**
 * Applies the events of one market's stream to the cache (DESIGN.md §14, "Live data").
 * Events are hints, not truth: one whose `version` is not newer than what is on screen
 * is dropped, and what belongs to the viewer is never worked out here but asked for.
 */
export function createMarketFollower(client: QueryClient, marketId: string) {
  const detailKey = keys.markets.detail(marketId);
  const historyKey = keys.markets.history(marketId);
  let newest: QuoteEvent | null = null;
  let catchingUp = false;

  /** Price, volume and the chart's next point, when the event is newer than the cache. */
  const show = (event: QuoteEvent) => {
    client.setQueryData<MarketDetailResponse>(detailKey, (market) =>
      market === undefined || event.version <= market.version
        ? market
        : {
            ...market,
            p_yes: event.p_yes,
            p_no: event.p_no,
            volume_cr: event.volume_cr,
            version: event.version,
          },
    );
    client.setQueryData<HistoryResponse>(historyKey, (history) => {
      if (history === undefined) return history;
      const last = history.points.at(-1);
      if (last !== undefined && event.version <= last.version) return history;
      const point = { t: event.ts, p_yes: event.p_yes, version: event.version };
      return { ...history, points: [...history.points, point] };
    });
  };

  /**
   * What a holding is worth and what can be sold are the server's to work out, so a
   * price that moved means asking for the market again. One request at a time; if events
   * overtook the answer, the newer price stays on screen and the market is asked once more.
   */
  const refreshViewer = async () => {
    if (catchingUp) return;
    catchingUp = true;
    try {
      for (let round = 0; round < CATCH_UP_ROUNDS; round += 1) {
        await client.invalidateQueries({ queryKey: detailKey, exact: true });
        const market = client.getQueryData<MarketDetailResponse>(detailKey);
        if (newest === null || market === undefined || market.version >= newest.version) return;
        show(newest);
      }
    } finally {
      catchingUp = false;
    }
  };

  /**
   * What a change of the market's status leaves out of date besides the market itself:
   * the lists it is in, and, once it is settled, everything that is the caller's own. A
   * resolution pays out and a void refunds, so the balance in the top bar is another now.
   */
  const statusChanged = (status: MarketStatus) => {
    void client.invalidateQueries({ queryKey: keys.markets.lists });
    if (status === "RESOLVED" || status === "VOID") {
      void client.invalidateQueries({ queryKey: keys.me });
    }
  };

  return {
    quote(event: QuoteEvent) {
      if (event.market_id !== marketId) return;
      if (newest === null || event.version > newest.version) newest = event;
      const before = client.getQueryData<MarketDetailResponse>(detailKey);
      show(event);
      const boughtIn = before !== undefined && !isZero(before.viewer.cost_cr);
      if (boughtIn && event.version > before.version) void refreshViewer();
    },

    /** An admin action changed the market: ask for it again, and for what follows from it. */
    status(event: StatusEvent) {
      if (event.market_id !== marketId) return;
      void client.invalidateQueries({ queryKey: detailKey, exact: true });
      statusChanged(event.status);
    },

    /**
     * The stream is down: ask for what it would have brought. A status that is another
     * one in the answer is treated as the `status` event that did not arrive.
     */
    async poll() {
      const before = client.getQueryData<MarketDetailResponse>(detailKey)?.status;
      void client.invalidateQueries({ queryKey: historyKey, exact: true });
      await client.invalidateQueries({ queryKey: detailKey, exact: true });
      const after = client.getQueryData<MarketDetailResponse>(detailKey)?.status;
      if (before !== undefined && after !== undefined && after !== before) statusChanged(after);
    },
  };
}

export type MarketStreamOptions = {
  /** False for a market that has no stream to follow: a proposal, a settled market. */
  enabled?: boolean;
  /**
   * Called for every `status` event of this market, after the market was asked for again.
   * It is how a screen knows that something happened in front of the user, which only the
   * event can say: the bean drop of a payout plays for a resolution that arrived like
   * this, and for no other (DESIGN.md §13).
   */
  onStatus?: (event: StatusEvent) => void;
};

/**
 * Keeps one market live while its screen is open: the price, the chart and the volume
 * follow `GET /markets/{market_id}/stream`, a `status` event refetches the market, and
 * with the stream down the market and its history are polled every
 * `live_poll_fallback_seconds`.
 *
 * It changes `marketQuery(marketId)` and `marketHistoryQuery(marketId)` in the cache; a
 * screen that reads those two needs nothing else. A market that settles also marks the
 * caller's own reads out of date, the balance among them. An open quote of the trade
 * sheet is never touched.
 */
export function useMarketStream(
  marketId: string,
  { enabled = true, onStatus }: MarketStreamOptions = {},
): { live: boolean } {
  const client = useQueryClient();
  const { live_poll_fallback_seconds } = useConfig();
  const follower = useMemo(() => createMarketFollower(client, marketId), [client, marketId]);

  return useEventStream({
    url: enabled ? `/api/markets/${encodeURIComponent(marketId)}/stream` : null,
    events: STREAM_EVENTS,
    pollSeconds: live_poll_fallback_seconds,
    onEvent: (name, data) => {
      if (name === "quote") {
        follower.quote(data as QuoteEvent);
      } else if (name === "status") {
        const event = data as StatusEvent;
        follower.status(event);
        if (event.market_id === marketId) onStatus?.(event);
      }
    },
    onPoll: () => void follower.poll(),
  });
}

const STREAM_EVENTS = ["quote", "status"] as const;
