import type { operations } from "@/lib/api/schema";

/**
 * The events of the event stream (DESIGN.md §14, "Live data"). Their bodies are the
 * contract's: `GET /stream` documents every event it sends as its answer, and the types
 * here are read off that answer. No body is typed a second time.
 */

/** One event: its body. Its `type` is the name the event travels under. */
export type LiveEvent = operations["stream_live"]["responses"][200]["content"]["text/event-stream"];

/** The name of an event: its `event:` line on the stream, and the `type` of its body. */
export type LiveEventName = LiveEvent["type"];

/** The body of the event of one name: `LiveEventOf<"market.quote">`. */
export type LiveEventOf<Name extends LiveEventName> = Extract<LiveEvent, { type: Name }>;

/**
 * Each name of the contract, once: a name that is missing here, and one the contract does
 * not have, fails to compile.
 */
const NAMES: { readonly [Name in LiveEventName]: Name } = {
  ready: "ready",
  beat: "beat",
  "market.quote": "market.quote",
  "market.status": "market.status",
  "leaderboard.changed": "leaderboard.changed",
  "me.changed": "me.changed",
};

/** Every name the stream sends, in the contract's order. The live client listens for each. */
export const LIVE_EVENT_NAMES: readonly LiveEventName[] = Object.values(NAMES);
