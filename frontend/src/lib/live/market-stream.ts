"use client";

import { type QueryClient, useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";

import { keys } from "../api/keys";
import type { HistoryResponse, MarketDetailResponse, QuoteEvent, StatusEvent } from "../api/schema";
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

  return {
    quote(event: QuoteEvent) {
      if (event.market_id !== marketId) return;
      if (newest === null || event.version > newest.version) newest = event;
      const before = client.getQueryData<MarketDetailResponse>(detailKey);
      show(event);
      const boughtIn = before !== undefined && !isZero(before.viewer.cost_cr);
      if (boughtIn && event.version > before.version) void refreshViewer();
    },

    /** An admin action changed the market: its detail, and the lists it is in, are out of date. */
    status(event: StatusEvent) {
      if (event.market_id !== marketId) return;
      void client.invalidateQueries({ queryKey: detailKey, exact: true });
      void client.invalidateQueries({ queryKey: keys.markets.lists });
    },

    /** The stream is down: ask for what it would have brought. */
    poll() {
      void client.invalidateQueries({ queryKey: detailKey, exact: true });
      void client.invalidateQueries({ queryKey: historyKey, exact: true });
    },
  };
}

/**
 * Keeps one market live while its screen is open: the price, the chart and the volume
 * follow `GET /markets/{market_id}/stream`, a `status` event refetches the market, and
 * with the stream down the market and its history are polled every
 * `live_poll_fallback_seconds`.
 *
 * It changes `marketQuery(marketId)` and `marketHistoryQuery(marketId)` in the cache; a
 * screen that reads those two needs nothing else. An open quote of the trade sheet is
 * never touched. Pass `enabled: false` for a market that has no stream to follow (a
 * proposal).
 */
export function useMarketStream(
  marketId: string,
  { enabled = true }: { enabled?: boolean } = {},
): { live: boolean } {
  const client = useQueryClient();
  const { live_poll_fallback_seconds } = useConfig();
  const follower = useMemo(() => createMarketFollower(client, marketId), [client, marketId]);

  return useEventStream({
    url: enabled ? `/api/markets/${encodeURIComponent(marketId)}/stream` : null,
    events: STREAM_EVENTS,
    pollSeconds: live_poll_fallback_seconds,
    onEvent: (name, data) => {
      if (name === "quote") follower.quote(data as QuoteEvent);
      else if (name === "status") follower.status(data as StatusEvent);
    },
    onPoll: follower.poll,
  });
}

const STREAM_EVENTS = ["quote", "status"] as const;
