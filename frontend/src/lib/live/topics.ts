/**
 * The topics of the event stream (DESIGN.md §14, "Live data"). A topic says what a screen
 * shows, and the stream sends what belongs to the topics it names. It is the text the
 * stream's address carries as `topic=`: a kind, or a kind and the id of a market.
 *
 * Naming a topic grants nothing: a market the viewer may not see, or one that does not
 * exist, sends no event.
 */
export type LiveTopic = "markets" | "leaderboard" | `market:${string}`;

const MARKET = "market:";

export const liveTopic = {
  /** Every market the viewer may see: what the Markets list shows. */
  markets: (): LiveTopic => "markets",
  /** One market: what Market detail shows. */
  market: (marketId: string): LiveTopic => `${MARKET}${marketId}`,
  /** The ranking: what the Leaderboard shows. */
  leaderboard: (): LiveTopic => "leaderboard",
} as const;

/** The id of the market a topic is about, and null for a topic that is about none. */
export function marketOfTopic(topic: LiveTopic): string | null {
  return topic.startsWith(MARKET) ? topic.slice(MARKET.length) : null;
}
