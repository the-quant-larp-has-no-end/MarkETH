import { describe, expect, test } from "vitest";

import { EN_DASH, TIMES } from "./amount";
import {
  formatAveragePrice,
  formatCount,
  formatPercent,
  formatRate,
  formatRatio,
  fractionToPercent,
  percentToFraction,
  spokenPercent,
} from "./percent";

describe("a probability is shown with the decimals of /config", () => {
  test("one decimal", () => {
    expect(formatPercent(0.548, 1)).toBe("54.8%");
    expect(formatPercent(0.5, 1)).toBe("50.0%");
    expect(formatPercent(1, 1)).toBe("100.0%");
    expect(formatPercent(0, 1)).toBe("0.0%");
  });

  test("another number of decimals", () => {
    expect(formatPercent(0.548, 0)).toBe("55%");
    expect(formatPercent(0.5483, 2)).toBe("54.83%");
  });

  test("a screen reader hears the word, not the sign", () => {
    expect(spokenPercent(0.548, 1)).toBe("54.8 percent");
  });
});

describe("the other figures the server sends as numbers", () => {
  test("an average price has two decimals more than a percentage and no percent sign", () => {
    expect(formatAveragePrice(0.524, 1)).toBe("0.524");
    expect(formatAveragePrice(0.5, 1)).toBe("0.500");
    expect(formatAveragePrice(0.5, 0)).toBe("0.50");
  });

  test("a win rate is a percentage, or an en dash while there is none", () => {
    expect(formatRate(0.625, 1)).toBe("62.5%");
    expect(formatRate(null, 1)).toBe(EN_DASH);
  });

  test("a payout ratio has two decimals and a multiplication sign, or an en dash", () => {
    expect(formatRatio(1.35)).toBe(`1.35${TIMES}`);
    expect(formatRatio(2)).toBe(`2.00${TIMES}`);
    expect(formatRatio(0)).toBe(`0.00${TIMES}`);
    expect(formatRatio(null)).toBe(EN_DASH);
  });

  test("a count is grouped in threes", () => {
    expect(formatCount(4)).toBe("4");
    expect(formatCount(1204)).toBe("1,204");
    expect(formatCount(1234567)).toBe("1,234,567");
  });
});

describe("a typed percentage becomes a fraction by moving the decimal point", () => {
  test("two places to the left, in the string", () => {
    expect(percentToFraction("62.5")).toBe("0.625");
    expect(percentToFraction("100")).toBe("1");
    expect(percentToFraction("50")).toBe("0.5");
    expect(percentToFraction("5")).toBe("0.05");
    expect(percentToFraction("0")).toBe("0");
    expect(percentToFraction("0.1")).toBe("0.001");
    expect(percentToFraction(" 12.50 ")).toBe("0.125");
    expect(percentToFraction("33.333333")).toBe("0.33333333");
  });

  test("what is no number is refused", () => {
    for (const text of ["", "abc", "12,5", "-5", "5%", "1e2", ".5", "5."]) {
      expect(percentToFraction(text)).toBeNull();
    }
  });

  test("and back, for showing a filter that is set", () => {
    expect(fractionToPercent("0.625")).toBe("62.5");
    expect(fractionToPercent("0.5")).toBe("50");
    expect(fractionToPercent("1")).toBe("100");
    expect(fractionToPercent("0.05")).toBe("5");
    expect(fractionToPercent("0")).toBe("0");
    expect(fractionToPercent("0.33333333")).toBe("33.333333");
    expect(fractionToPercent("high")).toBeNull();
  });

  test("the two are each other's inverse", () => {
    for (const typed of ["62.5", "100", "0", "7", "12.345"]) {
      expect(fractionToPercent(percentToFraction(typed)!)).toBe(typed);
    }
  });
});
