import { describe, expect, test } from "vitest";

import type { SellPreset } from "@/lib/api/schema";
import { marketViewer, marketViewerWithPosition } from "@/test/fixtures";

import { canSell, grant } from "./trade-surface";

const BUY_YES = { side: "BUY", outcome: "YES" } as const;
const BUY_NO = { side: "BUY", outcome: "NO" } as const;
const SELL_YES = { side: "SELL", outcome: "YES" } as const;
const SELL_NO = { side: "SELL", outcome: "NO" } as const;

const presets = (yes: boolean, no: boolean): SellPreset[] => [
  { outcome: "YES", portion: "HALF", shares: "45.8225", allowed: yes },
  { outcome: "YES", portion: "ALL", shares: "91.645", allowed: yes },
  { outcome: "NO", portion: "HALF", shares: "10", allowed: no },
  { outcome: "NO", portion: "ALL", shares: "20", allowed: no },
];

const blocked = marketViewer({ can_trade: false, cannot_trade_reason: "MARKET_NOT_OPEN" });

describe("canSell", () => {
  test("is true when at least one sell preset is allowed", () => {
    expect(canSell(marketViewerWithPosition())).toBe(true);
    expect(canSell(marketViewer())).toBe(false);
  });

  test("looks at one outcome when one is named", () => {
    const viewer = marketViewer({ sell_presets: presets(true, false) });

    expect(canSell(viewer, "YES")).toBe(true);
    expect(canSell(viewer, "NO")).toBe(false);
  });

  test("one allowed portion of an outcome is enough", () => {
    const viewer = marketViewer({
      sell_presets: [
        // Half of a small holding is below the minimum trade; all of it is not.
        { outcome: "YES", portion: "HALF", shares: "0.75", allowed: false },
        { outcome: "YES", portion: "ALL", shares: "1.5", allowed: true },
        { outcome: "NO", portion: "HALF", shares: "0", allowed: false },
        { outcome: "NO", portion: "ALL", shares: "0", allowed: false },
      ],
    });

    expect(canSell(viewer, "YES")).toBe(true);
  });

  test("reads the allowed flags and nothing else: shares that are held but not allowed cannot be sold", () => {
    const viewer = marketViewer({ sell_presets: presets(false, false) });

    expect(canSell(viewer)).toBe(false);
  });
});

describe("grant: what a request of the address leads to", () => {
  test("a buy is shown when the viewer can trade", () => {
    expect(grant(marketViewer(), BUY_YES, false)).toEqual(BUY_YES);
    expect(grant(marketViewer(), BUY_NO, false)).toEqual(BUY_NO);
  });

  test("a buy the viewer cannot make opens no sheet", () => {
    expect(grant(blocked, BUY_YES, false)).toBeNull();
  });

  test("where the panel is in place a buy is shown whatever can_trade says", () => {
    expect(grant(blocked, BUY_NO, true)).toEqual(BUY_NO);
  });

  test("a sell is shown when a preset of its outcome is allowed", () => {
    const viewer = marketViewer({ sell_presets: presets(true, true) });

    expect(grant(viewer, SELL_YES, false)).toEqual(SELL_YES);
    expect(grant(viewer, SELL_NO, false)).toEqual(SELL_NO);
  });

  test("a sell of an outcome that cannot be sold shows the one that can", () => {
    expect(grant(marketViewer({ sell_presets: presets(true, false) }), SELL_NO, false)).toEqual(
      SELL_YES,
    );
    expect(grant(marketViewer({ sell_presets: presets(false, true) }), SELL_YES, false)).toEqual(
      SELL_NO,
    );
  });

  test("a sell with no preset that is allowed opens nothing, with or without the panel", () => {
    expect(grant(marketViewer(), SELL_YES, false)).toBeNull();
    expect(grant(marketViewer(), SELL_YES, true)).toBeNull();
    expect(grant(blocked, SELL_NO, true)).toBeNull();
  });
});
