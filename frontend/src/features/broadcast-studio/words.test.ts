import { describe, expect, test } from "vitest";

import { broadcastConfig } from "@/test/fixtures";

import { endReasonSentence, minutesOf, secondsOf } from "./words";

describe("the sentence of an end reason", () => {
  const config = broadcastConfig({ stall_seconds: 45, max_minutes: 1500 });

  test.each([
    ["STALLED", "No video arrived for 45 seconds."],
    // A figure of four digits is grouped, as every count is.
    ["TIME_LIMIT", "It reached the limit of 1,500 minutes."],
    ["ADMIN", "VIS ended it."],
  ] as const)("%s: %s", (reason, sentence) => {
    expect(endReasonSentence(reason, config)).toBe(sentence);
  });

  test("a live stream its broadcaster ended, and one that has not ended, have none", () => {
    expect(endReasonSentence("BROADCASTER", config)).toBeNull();
    expect(endReasonSentence(null, config)).toBeNull();
  });
});

describe("a figure of /config in a sentence", () => {
  test("one of them is said in the singular", () => {
    expect(secondsOf(1)).toBe("1 second");
    expect(minutesOf(1)).toBe("1 minute");
    expect(endReasonSentence("STALLED", broadcastConfig({ stall_seconds: 1 }))).toBe(
      "No video arrived for 1 second.",
    );
  });

  test("every other number is said in the plural", () => {
    expect(secondsOf(120)).toBe("120 seconds");
    expect(minutesOf(90)).toBe("90 minutes");
  });
});
