import { describe, expect, test } from "vitest";

import { codeCharacters, formatCode, readScannedCode } from "./code";

describe("a code is shown in groups of four", () => {
  test("XXXX-XXXX-XXXX for a code, XXXX-XXXX for a voucher code", () => {
    expect(formatCode("4F7KQ2M9XAB3")).toBe("4F7K-Q2M9-XAB3");
    expect(formatCode("7KQ2M9XA")).toBe("7KQ2-M9XA");
  });

  test("whatever the API or the user put between the characters", () => {
    expect(formatCode("4F7K-Q2M9-XAB3")).toBe("4F7K-Q2M9-XAB3");
    expect(formatCode("4f7k q2m9 xab3")).toBe("4F7K-Q2M9-XAB3");
    expect(codeCharacters(" 4f7k-q2m9 ")).toBe("4F7KQ2M9");
  });

  test("the dashes appear as the user types", () => {
    expect(formatCode("")).toBe("");
    expect(formatCode("4")).toBe("4");
    expect(formatCode("4f7k")).toBe("4F7K");
    expect(formatCode("4f7kq")).toBe("4F7K-Q");
    expect(formatCode("4F7K-")).toBe("4F7K");
  });

  test("a field takes no more characters than the length /config gives", () => {
    expect(formatCode("4F7KQ2M9XAB3ZZZZ", 12)).toBe("4F7K-Q2M9-XAB3");
    expect(formatCode("7KQ2M9XAZZ", 8)).toBe("7KQ2-M9XA");
  });
});

describe("what a scanned QR holds of a code", () => {
  test("a code card's link gives its code", () => {
    expect(readScannedCode("https://09.hackathon.ethz.ch/wallet?code=4F7KQ2M9XAB3")).toBe(
      "4F7KQ2M9XAB3",
    );
    expect(readScannedCode("http://localhost:8080/wallet?code=4F7K-Q2M9-XAB3&x=1")).toBe(
      "4F7K-Q2M9-XAB3",
    );
  });

  test("what is no link is taken as it is", () => {
    expect(readScannedCode("4F7K-Q2M9-XAB3")).toBe("4F7K-Q2M9-XAB3");
    expect(readScannedCode("  7KQ2M9XA\n")).toBe("7KQ2M9XA");
  });

  test("a link without a code fills nothing", () => {
    expect(readScannedCode("https://09.hackathon.ethz.ch/wallet")).toBeNull();
    expect(readScannedCode("https://09.hackathon.ethz.ch/wallet?code=")).toBeNull();
    expect(readScannedCode("https://example.org/?voucher=4F7KQ2M9XAB3")).toBeNull();
    expect(readScannedCode("")).toBeNull();
  });

  test("a link is never returned, so it can never be sent to the API", () => {
    const scans = [
      "https://09.hackathon.ethz.ch/wallet?code=4F7KQ2M9XAB3",
      "https://09.hackathon.ethz.ch/wallet",
      "mailto:someone@example.org",
      "WIFI:T:WPA;S:eth;P:secret;;",
    ];

    for (const scan of scans) {
      expect(readScannedCode(scan) ?? "").not.toMatch(/[:/]/);
    }
  });
});
