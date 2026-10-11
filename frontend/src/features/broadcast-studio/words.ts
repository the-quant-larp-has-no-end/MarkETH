import type { BroadcastConfig, BroadcastEndReason } from "@/lib/api/schema";
import { formatCount } from "@/lib/format";

/**
 * The words of the studio for the two figures of `/config` `broadcast` that end a live
 * stream by itself, and for the reason a stream ended. The figures are the API's; here
 * they are only said.
 */

/** "120 seconds", "1 second". */
export function secondsOf(seconds: number): string {
  return seconds === 1 ? "1 second" : `${formatCount(seconds)} seconds`;
}

/** "120 minutes", "1 minute". */
export function minutesOf(minutes: number): string {
  return minutes === 1 ? "1 minute" : `${formatCount(minutes)} minutes`;
}

/**
 * `end_reason` is a key, not copy: this is its sentence (DESIGN.md §13, "Live stream"). A
 * live stream its broadcaster ended has none.
 */
export function endReasonSentence(
  reason: BroadcastEndReason | null,
  config: BroadcastConfig,
): string | null {
  switch (reason) {
    case "STALLED":
      return `No video arrived for ${secondsOf(config.stall_seconds)}.`;
    case "TIME_LIMIT":
      return `It reached the limit of ${minutesOf(config.max_minutes)}.`;
    case "ADMIN":
      return "VIS ended it.";
    case "BROADCASTER":
    case null:
      return null;
  }
}
