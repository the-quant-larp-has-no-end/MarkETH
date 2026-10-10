import { act, waitFor } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { MARKET_ID, marketViewer, OTHER_MARKET_ID, quote, tradeResponse } from "@/test/fixtures";
import { apiError, deferred, json, mockApi, noAnswer, type Route } from "@/test/http";
import { createTestQueryClient, renderHookWithProviders } from "@/test/render";

import { type Order, ticketStore } from "./ticket-store";
import { useTradeOut } from "./use-ticket";

const TRADE: Route = "POST /api/markets/{market_id}/trades";

const viewer = marketViewer();

/** Buying YES for 25, guarded by the quote of the fixtures. */
const buy: Order = {
  intent: { side: "BUY", outcome: "YES" },
  request: { side: "BUY", outcome: "YES", amount_cr: "25" },
  quote: quote(),
  stakePresets: viewer.stake_presets,
  buySlider: viewer.buy_slider,
  sellPresets: viewer.sell_presets,
};

/** The hook as the surface of a sheet uses it, beside the store its ticket writes to. */
function follow(shown: boolean, marketId = MARKET_ID) {
  const client = createTestQueryClient();
  const store = ticketStore(client);
  const view = renderHookWithProviders(
    ({ open }: { open: boolean }) => useTradeOut(marketId, open),
    { client, initialProps: { open: shown } },
  );
  return { store, ...view };
}

describe("useTradeOut", () => {
  test("is true from Confirm until the answer, and false before and after", async () => {
    const answer = deferred();
    mockApi({ [TRADE]: answer.handler });
    const { store, result } = follow(true);
    expect(result.current).toBe(false);

    act(() => store.confirm(MARKET_ID, buy));
    expect(result.current).toBe(true);

    await act(async () => answer.resolve(json(201, tradeResponse())));
    await waitFor(() => expect(result.current).toBe(false));
  });

  test("is false again once the trade was refused", async () => {
    mockApi({ [TRADE]: apiError(409, "INSUFFICIENT_BALANCE", "Not enough tokens for this.") });
    const { store, result } = follow(true);

    act(() => store.confirm(MARKET_ID, buy));

    await waitFor(() => expect(result.current).toBe(false));
    expect(store.read(MARKET_ID).refusal).not.toBeNull();
  });

  test("a trade that waits for Try again is not on its way: the sheet can be put away, and is held again by the next attempt", async () => {
    const api = mockApi({ [TRADE]: noAnswer });
    const { store, result } = follow(true);
    act(() => store.confirm(MARKET_ID, buy));
    await waitFor(() => expect(store.read(MARKET_ID).attempt?.sending).toBe(false));
    expect(result.current).toBe(false);

    const again = deferred();
    api.on(TRADE, again.handler);
    act(() => store.retry(MARKET_ID));
    expect(result.current).toBe(true);

    await act(async () => again.resolve(json(201, tradeResponse())));
    await waitFor(() => expect(result.current).toBe(false));
  });

  test("is about its own market: another market's trade does not hold this sheet", () => {
    mockApi({ [TRADE]: deferred().handler });
    const { store, result } = follow(true);

    act(() => store.confirm(OTHER_MARKET_ID, buy));

    expect(result.current).toBe(false);
  });

  test("says nothing while the sheet is closed, and does not follow the ticket then", async () => {
    mockApi({ [TRADE]: json(201, tradeResponse()) });
    const { store, result } = follow(false);

    act(() => store.confirm(MARKET_ID, buy));
    expect(result.current).toBe(false);
    await waitFor(() => expect(store.read(MARKET_ID).attempt).toBeNull());

    // Nobody showed the ticket, so its result was said with a toast and nothing is left
    // behind. Had the hook followed the ticket, the store would hold a receipt for it.
    expect(store.read(MARKET_ID).receipt).toBeNull();
  });

  test("while the sheet is open it counts as showing the ticket, and stops when the sheet closes", async () => {
    const answer = deferred();
    mockApi({ [TRADE]: answer.handler });
    const { store, result, rerender } = follow(true);
    act(() => store.confirm(MARKET_ID, buy));
    expect(result.current).toBe(true);

    // The sheet closes while the trade is out, which only leaving the page can do.
    rerender({ open: false });
    expect(result.current).toBe(false);
    await act(async () => answer.resolve(json(201, tradeResponse())));

    await waitFor(() => expect(store.read(MARKET_ID).attempt).toBeNull());
    expect(store.read(MARKET_ID).receipt).toBeNull();
  });

  test("opened onto a trade that is on its way, it says so at once", () => {
    mockApi({ [TRADE]: deferred().handler });
    const { store, result, rerender } = follow(false);
    act(() => store.confirm(MARKET_ID, buy));
    expect(result.current).toBe(false);

    rerender({ open: true });

    expect(result.current).toBe(true);
  });
});
