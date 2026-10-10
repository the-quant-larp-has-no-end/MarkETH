/**
 * What the scanner has sent to the API and has not lost sight of since (DESIGN.md §13).
 *
 * A voucher is held to the camera for longer than its scan takes, and it is still there
 * when its result has gone. It is not sent a second time for that: a QR that was sent is
 * a scan of its own again only once it has been out of sight for `awayMs`. Until then
 * every sight of it is noted and nothing goes out.
 *
 * - **Every QR that was sent is remembered, not only the latest.** Two vouchers in front of
 *   the camera at once are read in turn, and each is sent once.
 * - **Out of sight is measured in time the scanner was watching.** While a result, a check
 *   or the typing sheet covers the camera view, nothing can be seen and no time passes: a
 *   voucher is not "away" because the screen was busy with its answer.
 *
 * What the API answers to a QR that does come back is the API's to say: the redemption
 * again for the barista who redeemed it a moment ago, "not valid" otherwise.
 */
export type ScanMemory = {
  /** The camera view is up and being read (true), or something covers it (false). */
  watching(now: number, watching: boolean): void;
  /**
   * Whether `text` is a QR that was sent and has not been out of sight since. It is in
   * sight now, which is noted.
   */
  holds(text: string, now: number): boolean;
  /** `text` was sent to the API at `now`. */
  note(text: string, now: number): void;
};

/** `now` is a clock that only moves forward, in milliseconds (`performance.now()`). */
export function createScanMemory(awayMs: number): ScanMemory {
  /** For each QR that was sent: the watching time at which it was last in sight. */
  const lastSeen = new Map<string, number>();
  /** The time spent watching, up to `since`. */
  let watched = 0;
  /** Since when the scanner has been watching without a break. Null while it is not. */
  let since: number | null = null;

  const watchedAt = (now: number) => watched + (since === null ? 0 : Math.max(0, now - since));

  /** Drops what has been out of sight for long enough to be a scan of its own again. */
  const forget = (at: number) => {
    for (const [text, seen] of lastSeen) {
      if (at - seen >= awayMs) lastSeen.delete(text);
    }
  };

  return {
    watching(now, watching) {
      if (watching === (since !== null)) return;
      if (watching) {
        since = now;
      } else {
        watched = watchedAt(now);
        since = null;
      }
    },
    holds(text, now) {
      const at = watchedAt(now);
      forget(at);
      if (!lastSeen.has(text)) return false;
      lastSeen.set(text, at);
      return true;
    },
    note(text, now) {
      lastSeen.set(text, watchedAt(now));
    },
  };
}
