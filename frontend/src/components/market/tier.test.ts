import { expect, test } from "vitest";

import { tierExplanation, tierName } from "./tier";

test("a tier is named in words, never by its key", () => {
  expect(tierName("TIGHT")).toBe("Tight");
  expect(tierName("DEFAULT")).toBe("Default");
  expect(tierName("MEDIUM")).toBe("Medium");
  expect(tierName("CUSTOM")).toBe("Custom");
});

test("the three tiers a proposal may ask for say what they do to the price", () => {
  expect(tierExplanation("TIGHT")).toBe(
    "The price moves fast. For a question few people will trade.",
  );
  expect(tierExplanation("DEFAULT")).toBe("Right for most questions.");
  expect(tierExplanation("MEDIUM")).toBe(
    "The price moves slowly. For a question many people will trade.",
  );
  // Custom is for VIS to choose at approval: there is nothing to tell a proposer.
  expect(tierExplanation("CUSTOM")).toBeUndefined();
});
