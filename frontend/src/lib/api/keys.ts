import type { MarketStatus, operations, Outcome } from "./schema";

/** What a quote is asked for: a budget when buying, a number of shares when selling. */
export type QuoteRequest =
  | { side: "BUY"; outcome: Outcome; amount_cr: string }
  | { side: "SELL"; outcome: Outcome; shares: string };

/** Sort, order and filters of `GET /leaderboard`, under the names the API and the address use. */
export type LeaderboardParams = Omit<
  NonNullable<operations["get_leaderboard"]["parameters"]["query"]>,
  "limit" | "cursor"
>;

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
