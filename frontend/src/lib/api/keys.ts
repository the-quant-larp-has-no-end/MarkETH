import type { MarketStatus, operations, Outcome } from "./schema";

/** What a quote is asked for: a budget when buying, a number of shares when selling. */
export type QuoteRequest =
  | { side: "BUY"; outcome: Outcome; amount_cr: string }
  | { side: "SELL"; outcome: Outcome; shares: string };

/** The query of `GET /leaderboard`, as the contract types it. */
export type LeaderboardQuery = NonNullable<operations["get_leaderboard"]["parameters"]["query"]>;

/**
 * The filters the contract types as a number or as a key of a fixed list. In a query
 * string each of them is its characters, and the screen sends what was typed or what the
 * address holds: the API is the judge of it (DESIGN.md §13).
 */
type SentAsText =
  | "min_trades"
  | "max_trades"
  | "min_win_rate"
  | "max_win_rate"
  | "min_avg_payout_ratio"
  | "max_avg_payout_ratio"
  | "degree_level";

/**
 * Sort, order and filters of `GET /leaderboard`, under the names the API and the address
 * use. No `limit` (the page size is the API's) and no `cursor` (the query pages itself).
 */
export type LeaderboardParams = Omit<LeaderboardQuery, "limit" | "cursor" | SentAsText> & {
  [Name in SentAsText]?: LeaderboardQuery[Name] | string;
};

/**
 * The key of every query. A key that is a prefix of others invalidates them with it:
 * `["me"]` covers the profile and the five lists under it, `["markets"]` every market
 * read, `["leaderboard"]` every sort and filter.
 */
export const keys = {
  config: ["config"],
  /** `GET /me`, and the prefix of everything else that is the caller's own. */
  me: ["me"],
  positions: ["me", "positions"],
  trades: ["me", "trades"],
  ledger: ["me", "ledger"],
  proposals: ["me", "proposals"],
  vouchers: ["me", "vouchers"],
  products: ["vouchers", "products"],
  markets: {
    all: ["markets"],
    /** Every list of markets, whatever its statuses. */
    lists: ["markets", "list"],
    /** One list. Without statuses: what the API lists by itself. */
    list: (statuses?: readonly MarketStatus[]) =>
      ["markets", "list", statuses === undefined ? "default" : [...statuses]] as const,
    detail: (marketId: string) => ["markets", "detail", marketId] as const,
    history: (marketId: string) => ["markets", "history", marketId] as const,
    /** Every quote of one market. */
    quotes: (marketId: string) => ["markets", "quote", marketId] as const,
    quote: (marketId: string, request: QuoteRequest) =>
      ["markets", "quote", marketId, request] as const,
  },
  leaderboard: {
    all: ["leaderboard"],
    list: (params: LeaderboardParams) => ["leaderboard", params] as const,
  },
  admin: {
    all: ["admin"],
    batches: ["admin", "code-batches"],
    risk: ["admin", "risk"],
  },
} as const;
