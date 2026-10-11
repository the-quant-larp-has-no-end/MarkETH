/**
 * Live data (DESIGN.md §14): the app's side of the one event stream. A component names
 * the topic it shows with `useLiveTopic(liveTopic.broadcast(marketId))` and reads the
 * cache; `useLiveEvent` hears the events of one name, and `useLive` says whether the
 * stream is open to code that reads differently without it.
 */
export { LIVE_EVENT_NAMES, type LiveEvent, type LiveEventName, type LiveEventOf } from "./events";
export { LiveProvider, useLive, useLiveEvent, useLiveTopic } from "./provider";
export { type LiveTopic, liveTopic } from "./topics";
