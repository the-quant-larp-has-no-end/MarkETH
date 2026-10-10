import { describe, expect, test } from "vitest";

import {
  cutDecimals,
  formatAmount,
  formatShares,
  formatTokens,
  groupDigits,
  isZero,
  MINUS,
  shareWord,
  splitSign,
  tokenWord,
} from "./amount";

describe("an amount reaches the screen as the string the API sent", () => {
  test("a short amount is shown unchanged", () => {
    expect(formatAmount("25")).toBe("25");
    expect(formatAmount("0")).toBe("0");
    expect(formatAmount("0.5")).toBe("0.5");
    expect(formatAmount("183.29")).toBe("183.29");
  });

  test("a long amount is cut after the second decimal, never rounded", () => {
    expect(formatAmount("1.999999")).toBe("1.99");
    expect(formatAmount("50.221461")).toBe("50.22");
    expect(formatAmount("0.009")).toBe("0.00");
    expect(cutDecimals("12.3456")).toBe("12.34");
    expect(cutDecimals("12.3456", 4)).toBe("12.3456");
    expect(cutDecimals("12.3456", 0)).toBe("12");
    expect(cutDecimals("12")).toBe("12");
  });

  test("the full string is kept where a detail view asks for it", () => {
    expect(formatAmount("45.123456", { full: true })).toBe("45.123456");
    expect(formatAmount("1250.000001", { full: true })).toBe("1,250.000001");
  });

  test("from 1,000 up the whole part is grouped in threes, by inserting commas", () => {
    expect(formatAmount("999")).toBe("999");
    expect(formatAmount("1000")).toBe("1,000");
    expect(formatAmount("1250.5")).toBe("1,250.5");
    expect(formatAmount("123456789012.123456")).toBe("123,456,789,012.12");
    expect(groupDigits("1000000")).toBe("1,000,000");
    expect(groupDigits("0.123456")).toBe("0.123456");
  });

  test("more digits than a number can hold survive untouched", () => {
    // 2^53 is 9007199254740992; a float would turn the last digits into something else.
    expect(formatAmount("9007199254740993", { full: true })).toBe("9,007,199,254,740,993");
    expect(formatAmount("999999999999.999999", { full: true })).toBe("999,999,999,999.999999");
  });

  test("a minus becomes the minus sign U+2212", () => {
    expect(formatAmount("-25")).toBe(`${MINUS}25`);
    expect(formatAmount("-1411.456789")).toBe(`${MINUS}1,411.45`);
    expect(splitSign("-25")).toEqual({ negative: true, magnitude: "25" });
    expect(splitSign("200")).toEqual({ negative: false, magnitude: "200" });
  });
});

describe("zero is the one thing read from an amount", () => {
  test("it is the single character 0 and nothing else", () => {
    expect(isZero("0")).toBe(true);
    expect(isZero("0.000001")).toBe(false);
    expect(isZero("10")).toBe(false);
    expect(isZero("")).toBe(false);
  });
});

describe("the word after a figure follows the string", () => {
  test("token when the string is 1, otherwise tokens", () => {
    expect(tokenWord("1")).toBe("token");
    expect(tokenWord("0")).toBe("tokens");
    expect(tokenWord("1.5")).toBe("tokens");
    expect(tokenWord("11")).toBe("tokens");
    expect(formatTokens("240")).toBe("240 tokens");
    expect(formatTokens("1")).toBe("1 token");
    expect(formatTokens("1250.5")).toBe("1,250.5 tokens");
  });

  test("shares are named the same way, with their outcome when one is given", () => {
    expect(shareWord("1")).toBe("share");
    expect(formatShares("183.29")).toBe("183.29 shares");
    expect(formatShares("1")).toBe("1 share");
    expect(formatShares("183.29", { outcome: "YES" })).toBe("183.29 YES shares");
    expect(formatShares("45.123456", { outcome: "NO", full: true })).toBe("45.123456 NO shares");
  });
});
