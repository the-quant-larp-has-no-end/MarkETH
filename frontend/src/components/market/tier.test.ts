import { expect, test } from "vitest";

import { tierName } from "./tier";

test("a tier is named in words, never by its key", () => {
  expect(tierName("TIGHT")).toBe("Tight");
  expect(tierName("DEFAULT")).toBe("Default");
  expect(tierName("MEDIUM")).toBe("Medium");
  expect(tierName("CUSTOM")).toBe("Custom");
});
