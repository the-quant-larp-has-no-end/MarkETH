import { describe, expect, test } from "vitest";

import { createScanMemory } from "./scan-memory";

const AWAY = 2000;
const VOUCHER = "7KQ2-M9XA";
const OTHER = "B4T8-WN3C";

/** A memory whose scanner is watching from the start, as the screen's is. */
function watchingMemory() {
  const memory = createScanMemory(AWAY);
  memory.watching(0, true);
  return memory;
}

describe("what the scanner has sent", () => {
  test("a QR nobody has sent is not held", () => {
    const memory = watchingMemory();

    expect(memory.holds(VOUCHER, 100)).toBe(false);
    // Looking at it did not make it one that was sent.
    expect(memory.holds(VOUCHER, 200)).toBe(false);
  });

  test("a QR that was sent is held for as long as it keeps being seen", () => {
    const memory = watchingMemory();
    memory.note(VOUCHER, 0);

    // Seen again and again, each time before it has been away: half a minute and more.
    for (let now = 600; now <= 45_000; now += 600) {
      expect(memory.holds(VOUCHER, now)).toBe(true);
    }
  });

  test("it is a scan of its own once it has been out of sight for the whole time", () => {
    const memory = watchingMemory();
    memory.note(VOUCHER, 0);

    expect(memory.holds(VOUCHER, AWAY - 1)).toBe(true);
    // Counted from its last sight, not from when it was sent.
    expect(memory.holds(VOUCHER, AWAY - 1 + AWAY - 1)).toBe(true);
    expect(memory.holds(VOUCHER, AWAY - 1 + AWAY - 1 + AWAY)).toBe(false);
  });

  test("once forgotten it stays forgotten until it is sent again", () => {
    const memory = watchingMemory();
    memory.note(VOUCHER, 0);

    expect(memory.holds(VOUCHER, AWAY)).toBe(false);
    expect(memory.holds(VOUCHER, AWAY + 10)).toBe(false);
    memory.note(VOUCHER, AWAY + 10);
    expect(memory.holds(VOUCHER, AWAY + 20)).toBe(true);
  });

  test("every QR that was sent is held, not only the latest: the camera reads now one, now the other", () => {
    const memory = watchingMemory();
    memory.note(VOUCHER, 0);
    memory.note(OTHER, 300);

    for (let now = 900; now <= 20_000; now += 1200) {
      expect(memory.holds(VOUCHER, now)).toBe(true);
      expect(memory.holds(OTHER, now + 600)).toBe(true);
    }
  });

  test("each QR is away by its own last sight", () => {
    const memory = watchingMemory();
    memory.note(VOUCHER, 0);
    memory.note(OTHER, 0);

    // One stays, the other is taken away.
    expect(memory.holds(VOUCHER, 1500)).toBe(true);
    expect(memory.holds(VOUCHER, 3000)).toBe(true);
    expect(memory.holds(OTHER, 3000)).toBe(false);
    expect(memory.holds(VOUCHER, 4500)).toBe(true);
  });
});

describe("time the camera view is covered", () => {
  test("does not count: a voucher is not away because the screen showed its result", () => {
    const memory = watchingMemory();
    memory.note(VOUCHER, 100);

    // The check and the result cover the view for longer than a QR may be away.
    memory.watching(100, false);
    memory.watching(100 + 60_000, true);

    expect(memory.holds(VOUCHER, 100 + 60_000 + AWAY - 1)).toBe(true);
  });

  test("the time before and after a cover adds up", () => {
    const memory = watchingMemory();
    memory.note(VOUCHER, 0);

    memory.watching(1500, false);
    memory.watching(9000, true);

    // 1500 ms watched before the cover, 499 ms after it: not away yet.
    expect(memory.holds(VOUCHER, 9499)).toBe(true);
    memory.watching(9499, false);
    memory.watching(20_000, true);
    expect(memory.holds(VOUCHER, 20_000 + AWAY)).toBe(false);
  });

  test("a sight while the view is covered is noted at the moment the cover came", () => {
    const memory = watchingMemory();
    memory.note(VOUCHER, 0);
    memory.watching(1000, false);

    expect(memory.holds(VOUCHER, 5000)).toBe(true);
    memory.watching(6000, true);
    expect(memory.holds(VOUCHER, 6000 + AWAY - 1)).toBe(true);
    expect(memory.holds(VOUCHER, 6000 + AWAY - 1 + AWAY)).toBe(false);
  });

  test("being told twice that it watches, or twice that it does not, changes nothing", () => {
    const memory = watchingMemory();
    memory.note(VOUCHER, 0);

    memory.watching(500, true);
    memory.watching(1000, false);
    memory.watching(4000, false);
    memory.watching(5000, true);
    memory.watching(5500, true);

    // 1000 ms before the cover and 999 ms after it.
    expect(memory.holds(VOUCHER, 5999)).toBe(true);
  });

  test("before the scanner has watched at all, nothing is away", () => {
    const memory = createScanMemory(AWAY);
    memory.note(VOUCHER, 0);

    expect(memory.holds(VOUCHER, 60_000)).toBe(true);
  });
});
