import { infiniteQueryOptions, queryOptions } from "@tanstack/react-query";

import { call } from "./client";
import { keys, type LeaderboardParams, type QuoteRequest } from "./keys";
import type { MarketStatus } from "./schema";

/**
 * One `queryOptions` for every read of the API. A screen passes them to `useQuery` or
 * `useInfiniteQuery` as they are; the types of the data come with them.
 *
 * Nothing here sets a page size: a list that pages is as long as the API makes it. A
 * query is stale at once, so it is asked again when a screen opens and when the window
 * gets the focus back (DESIGN.md §14, "Live data").
 */

/** `GET /config`: the tunables of the product. They do not change while the page is open. */
export const configQuery = queryOptions({
  queryKey: keys.config,
  queryFn: ({ signal }) => call((api) => api.GET("/api/config", { signal })),
  staleTime: Infinity,
});

/** `GET /me`: the caller's profile, role and balance. */
export const meQuery = queryOptions({
  queryKey: keys.me,
  queryFn: ({ signal }) => call((api) => api.GET("/api/me", { signal })),
});

/**
 * `GET /markets`. Without `statuses` the API lists every status but PROPOSED and
 * REJECTED; with them, exactly those.
 */
export const marketsQuery = (statuses?: readonly MarketStatus[]) =>
  queryOptions({
    queryKey: keys.markets.list(statuses),
    queryFn: ({ signal }) =>
      call((api) =>
        api.GET("/api/markets", {
          params: { query: { status: statuses === undefined ? undefined : [...statuses] } },
          signal,
        }),
      ),
  });

/** `GET /markets/{market_id}`: the market with the viewer's side of it. */
export const marketQuery = (marketId: string) =>
  queryOptions({
    queryKey: keys.markets.detail(marketId),
    queryFn: ({ signal }) =>
      call((api) =>
        api.GET("/api/markets/{market_id}", { params: { path: { market_id: marketId } }, signal }),
      ),
  });

/** `GET /markets/{market_id}/history`: the points of the probability chart. */
export const marketHistoryQuery = (marketId: string) =>
  queryOptions({
    queryKey: keys.markets.history(marketId),
    queryFn: ({ signal }) =>
      call((api) =>
        api.GET("/api/markets/{market_id}/history", {
          params: { path: { market_id: marketId } },
          signal,
        }),
      ),
  });

/**
 * `GET /markets/{market_id}/quote`. A quote is what the user confirms, so nothing asks
 * for it again behind their back: not the focus, not a reconnect, not a retry. The trade
 * sheet asks when its input changes, and the server answers `QUOTE_STALE` if the price
 * has moved since (DESIGN.md §14).
 */
export const quoteQuery = (marketId: string, request: QuoteRequest) =>
  queryOptions({
    queryKey: keys.markets.quote(marketId, request),
    queryFn: ({ signal }) =>
      call((api) =>
        api.GET("/api/markets/{market_id}/quote", {
          params: { path: { market_id: marketId }, query: request },
          signal,
        }),
      ),
    staleTime: Infinity,
    gcTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });

/** `GET /me/positions`: the portfolio. */
export const positionsQuery = queryOptions({
  queryKey: keys.positions,
  queryFn: ({ signal }) => call((api) => api.GET("/api/me/positions", { signal })),
});

/** `GET /me/trades`, page by page. */
export const tradesQuery = infiniteQueryOptions({
  queryKey: keys.trades,
  queryFn: ({ pageParam, signal }) =>
    call((api) => api.GET("/api/me/trades", { params: { query: { cursor: pageParam } }, signal })),
  initialPageParam: null as string | null,
  getNextPageParam: (page) => page.next_cursor ?? undefined,
});

/** `GET /me/ledger`, page by page. */
export const ledgerQuery = infiniteQueryOptions({
  queryKey: keys.ledger,
  queryFn: ({ pageParam, signal }) =>
    call((api) => api.GET("/api/me/ledger", { params: { query: { cursor: pageParam } }, signal })),
  initialPageParam: null as string | null,
  getNextPageParam: (page) => page.next_cursor ?? undefined,
});

/** `GET /me/proposals`: the caller's own proposals. */
export const proposalsQuery = queryOptions({
  queryKey: keys.proposals,
  queryFn: ({ signal }) => call((api) => api.GET("/api/me/proposals", { signal })),
});

/** `GET /me/vouchers`. A voucher in this list never carries its code. */
export const vouchersQuery = queryOptions({
  queryKey: keys.vouchers,
  queryFn: ({ signal }) => call((api) => api.GET("/api/me/vouchers", { signal })),
});

/** `GET /vouchers/products`: what a voucher can be for, each with `affordable`. */
export const productsQuery = queryOptions({
  queryKey: keys.products,
  queryFn: ({ signal }) => call((api) => api.GET("/api/vouchers/products", { signal })),
});

/**
 * `GET /leaderboard`, page by page. `params` are sent as they are: the server sorts,
 * filters and ranks, and every page carries the `viewer` row.
 */
export const leaderboardQuery = (params: LeaderboardParams) =>
  infiniteQueryOptions({
    queryKey: keys.leaderboard.list(params),
    queryFn: ({ pageParam, signal }) =>
      call((api) =>
        api.GET("/api/leaderboard", {
          params: { query: { ...params, cursor: pageParam } },
          signal,
        }),
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });

/** `GET /admin/code-batches`. */
export const codeBatchesQuery = queryOptions({
  queryKey: keys.admin.batches,
  queryFn: ({ signal }) => call((api) => api.GET("/api/admin/code-batches", { signal })),
});

/** `GET /admin/risk`: every figure of the risk page, and the cost of a void. */
export const riskQuery = queryOptions({
  queryKey: keys.admin.risk,
  queryFn: ({ signal }) => call((api) => api.GET("/api/admin/risk", { signal })),
});
