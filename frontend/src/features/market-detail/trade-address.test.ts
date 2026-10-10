import { describe, expect, test } from "vitest";

import { intentHref, readIntent, sameIntent } from "./trade-address";

const search = (query: string) => new URLSearchParams(query);
const PATH = "/markets/3f6c2f0e-8b1d-4c57-9a43-0d6f1e2b7a10";

describe("readIntent", () => {
  test.each([
    ["buy=YES", { side: "BUY", outcome: "YES" }],
    ["buy=NO", { side: "BUY", outcome: "NO" }],
    ["sell=YES", { side: "SELL", outcome: "YES" }],
    ["sell=NO", { side: "SELL", outcome: "NO" }],
  ] as const)("?%s asks for that trade", (query, intent) => {
    expect(readIntent(search(query))).toEqual(intent);
  });

  test.each(["", "buy=", "buy=yes", "buy=MAYBE", "sell=ALL", "trade=YES", "buy=YES%20"])(
    "?%s asks for none",
    (query) => {
      expect(readIntent(search(query))).toBeNull();
    },
  );

  test("reads the request among whatever else the address holds", () => {
    expect(readIntent(search("from=portfolio&sell=NO"))).toEqual({ side: "SELL", outcome: "NO" });
  });

  test("of a buy and a sell in one address, the buy counts", () => {
    expect(readIntent(search("sell=NO&buy=YES"))).toEqual({ side: "BUY", outcome: "YES" });
    // A buy that says nothing leaves the sell.
    expect(readIntent(search("buy=maybe&sell=NO"))).toEqual({ side: "SELL", outcome: "NO" });
  });
});

describe("intentHref", () => {
  test("puts the trade into the address of the market", () => {
    expect(intentHref(PATH, search(""), { side: "BUY", outcome: "YES" })).toBe(`${PATH}?buy=YES`);
    expect(intentHref(PATH, search(""), { side: "SELL", outcome: "NO" })).toBe(`${PATH}?sell=NO`);
  });

  test("replaces the trade that was there, whatever its side", () => {
    expect(intentHref(PATH, search("buy=YES"), { side: "SELL", outcome: "YES" })).toBe(
      `${PATH}?sell=YES`,
    );
    expect(intentHref(PATH, search("sell=NO"), { side: "BUY", outcome: "NO" })).toBe(
      `${PATH}?buy=NO`,
    );
  });

  test("without a trade it is the plain address of the market", () => {
    expect(intentHref(PATH, search("buy=YES"), null)).toBe(PATH);
    expect(intentHref(PATH, search("sell=NO&buy=YES"), null)).toBe(PATH);
  });

  test("keeps what else the address holds", () => {
    expect(intentHref(PATH, search("from=portfolio&buy=YES"), null)).toBe(`${PATH}?from=portfolio`);
    expect(intentHref(PATH, search("from=portfolio"), { side: "BUY", outcome: "NO" })).toBe(
      `${PATH}?from=portfolio&buy=NO`,
    );
  });

  test("is an address on the page's own origin", () => {
    expect(intentHref(PATH, search(""), { side: "BUY", outcome: "YES" })).toMatch(/^\/markets\//);
  });

  test("what it writes is what readIntent reads", () => {
    for (const side of ["BUY", "SELL"] as const) {
      for (const outcome of ["YES", "NO"] as const) {
        const href = intentHref(PATH, search(""), { side, outcome });
        expect(readIntent(new URL(href, "http://localhost").searchParams)).toEqual({
          side,
          outcome,
        });
      }
    }
  });
});

describe("sameIntent", () => {
  test("compares side and outcome", () => {
    expect(sameIntent({ side: "BUY", outcome: "YES" }, { side: "BUY", outcome: "YES" })).toBe(true);
    expect(sameIntent({ side: "BUY", outcome: "YES" }, { side: "BUY", outcome: "NO" })).toBe(false);
    expect(sameIntent({ side: "BUY", outcome: "YES" }, { side: "SELL", outcome: "YES" })).toBe(
      false,
    );
  });
});
