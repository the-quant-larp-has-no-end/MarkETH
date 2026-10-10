import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import { keys } from "@/lib/api/keys";
import type { MarketDetailResponse } from "@/lib/api/schema";
import {
  adminMarketDetail,
  holdings,
  MARKET_ID,
  marketDetail,
  marketViewer,
  marketViewerWithPosition,
  me,
  quote,
  tradeResponse,
} from "@/test/fixtures";
import { apiError, deferred, json } from "@/test/http";

import {
  BUY_NO,
  BUY_YES,
  button,
  MARKET,
  ME,
  openTicket,
  outcomeSegment,
  QUOTE,
  row,
  SELL_NO,
  SELL_YES,
  sellChip,
  sharesField,
  sharesRefusal,
  sharesSlider,
  sideSegment,
  stakeChip,
  stakeField,
  stakeRefusal,
  stakeSlider,
  ticketFrame,
  TRADE,
} from "./test-harness";

/** A market whose viewer holds YES shares and can sell half or all of them. */
const withPosition = () => marketDetail({ viewer: marketViewerWithPosition() });

/** Fails unless each element comes after the one before it in the document. */
function expectInOrder(elements: Element[]): void {
  for (let index = 1; index < elements.length; index += 1) {
    const follows =
      elements[index - 1]!.compareDocumentPosition(elements[index]!) &
      Node.DOCUMENT_POSITION_FOLLOWING;
    expect(follows, `element ${index} follows element ${index - 1}`).toBeTruthy();
  }
}

const chipNames = (group: string) =>
  within(screen.getByRole("group", { name: group }))
    .getAllByRole("button")
    .map((chip) => chip.textContent);

describe("the form", () => {
  test("shows, top to bottom: Buy and Sell, the outcome, the stake entry with what is left, the quote, the balance and Confirm", () => {
    const market = marketDetail({
      viewer: marketViewer({ balance_cr: "240", stake_left_cr: "74.123456" }),
    });
    openTicket({ market });

    expect(sideSegment("Buy")).toHaveAttribute("aria-checked", "true");
    expect(sideSegment("Sell")).toHaveAttribute("aria-checked", "false");
    expect(outcomeSegment("YES")).toHaveAttribute("aria-pressed", "true");
    expect(outcomeSegment("NO")).toHaveAttribute("aria-pressed", "false");

    const stake = screen.getByRole("group", { name: "Stake" });
    expect(stake).toContainElement(stakeSlider());
    expect(stake).toContainElement(stakeField());
    expect(chipNames("Quick amounts")).toEqual(["2525 tokens", "5050 tokens", "100100 tokens"]);
    for (const amount of ["25", "50", "100"]) {
      expect(stakeChip(amount)).toBeEnabled();
      expect(stakeChip(amount)).toHaveAttribute("aria-pressed", "false");
    }
    const stakeLeft = screen.getByText("74.12 of 100 tokens left in this market");
    expect(stake).toContainElement(stakeLeft);

    const balance = row("Balance");
    expect(balance).toHaveTextContent("240 tokens");

    const confirm = button("Buy YES");
    expect(confirm).toBeDisabled();

    expectInOrder([
      sideSegment("Buy"),
      outcomeSegment("YES"),
      stakeSlider(),
      stakeField(),
      stakeChip("25"),
      stakeLeft,
      row("You pay"),
      row("New probability"),
      screen.getByText("Each share pays 1 token if YES wins."),
      balance,
      confirm,
    ]);
  });

  test("has no question of its own: the surface it stands on has it in its header", () => {
    const market = marketDetail();
    openTicket({ market });

    expect(screen.queryByText(market.question)).not.toBeInTheDocument();
  });

  test("opens with an empty field: the quote rows are there without figures, and nothing is asked", async () => {
    const { api } = openTicket();

    expect(stakeField()).toHaveValue("");
    expect(stakeSlider()).toHaveAttribute("aria-valuetext", "not set");

    for (const label of ["You pay", "Shares", "Average price", "New probability"]) {
      expect(within(row(label)).getByText("none yet")).toBeInTheDocument();
      expect(row(label).querySelector("[data-slot=skeleton]")).not.toBeInTheDocument();
    }
    expect(document.querySelector("[data-slot=quote-panel] dl")).not.toHaveAttribute("aria-busy");

    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(api.calls(QUOTE)).toHaveLength(0);
  });

  test("Confirm is 56 px, as wide as the sheet, and its fill follows the outcome when buying", async () => {
    const { user } = openTicket();

    expect(button("Buy YES")).toHaveClass("h-14", "w-full", "bg-yes", "text-ink");

    await user.click(outcomeSegment("NO"));

    expect(button("Buy NO")).toHaveClass("h-14", "w-full", "bg-no", "text-ink");
    expect(screen.getByText("Each share pays 1 token if NO wins.")).toBeInTheDocument();
  });

  test("the presets are the market's, and only the server's flag disables one", () => {
    const viewer = marketViewer({
      // The smallest is refused and a larger one allowed: no rule of the frontend's would say so.
      stake_presets: [
        { amount_cr: "10", allowed: false },
        { amount_cr: "1250.5", allowed: true },
        { amount_cr: "40", allowed: false },
      ],
    });
    openTicket({
      market: marketDetail({ viewer }),
      // The presets come from the market, not from /config.
      tunables: { stake_presets_cr: ["1", "2", "3"] },
    });

    expect(chipNames("Quick amounts")).toEqual([
      "1010 tokens",
      "1,250.51,250.5 tokens",
      "4040 tokens",
    ]);
    expect(stakeChip("10")).toBeDisabled();
    expect(stakeChip("1,250.5")).toBeEnabled();
    expect(stakeChip("40")).toBeDisabled();
  });

  test("Sell is offered when one sell preset is allowed, and not otherwise", () => {
    const { unmount } = openTicket();
    expect(sideSegment("Sell")).toBeDisabled();
    unmount();

    openTicket({ market: withPosition() });
    expect(sideSegment("Sell")).toBeEnabled();
  });

  test("tells its owner when the side or the outcome changes, and shows what the owner sets", async () => {
    const { user, changes } = openTicket({ market: withPosition() });

    await user.click(outcomeSegment("NO"));
    expect(changes).toEqual([BUY_NO]);
    expect(outcomeSegment("NO")).toHaveAttribute("aria-pressed", "true");
    expect(ticketFrame()).toHaveAttribute("data-outcome", "NO");

    await user.click(outcomeSegment("YES"));
    await user.click(sideSegment("Sell"));
    expect(changes).toEqual([BUY_NO, BUY_YES, SELL_YES]);
    expect(sideSegment("Sell")).toHaveAttribute("aria-checked", "true");
    expect(ticketFrame()).toHaveAttribute("data-side", "SELL");
  });

  test("holds no h1: the screen it stands on has the one", () => {
    openTicket();

    expect(screen.queryByRole("heading", { level: 1 })).not.toBeInTheDocument();
  });

  test("the slider runs over the range the market sends for it", () => {
    openTicket({
      market: marketDetail({ viewer: marketViewer({ buy_slider: { min: 5, max: 85, step: 5 } }) }),
      // The range comes from the market, not from /config.
      tunables: { stake_step_cr: "1", max_stake_per_market_cr: "100" },
    });

    expect(stakeSlider()).toHaveAttribute("aria-valuemin", "5");
    expect(stakeSlider()).toHaveAttribute("aria-valuemax", "85");
  });

  test("without a range from the market there is no slider; the field and the presets stay", () => {
    openTicket({
      // Less than a whole step of the slider is left: the server sends no range, and says
      // that an amount can still be bought. Nothing here works that out.
      market: marketDetail({
        viewer: marketViewer({ stake_left_cr: "1.5", buy_slider: null, can_buy: true }),
      }),
    });

    expect(screen.queryByRole("slider")).not.toBeInTheDocument();
    expect(stakeField()).toBeEnabled();
    expect(stakeChip("25")).toBeInTheDocument();
  });

  test("brings its own scrolling body and its pinned footer, with Confirm in the footer", () => {
    openTicket();

    const body = ticketFrame()!.querySelector("[data-slot=sheet-body]")!;
    const footer = ticketFrame()!.querySelector("[data-slot=sheet-footer]")!;
    expect(body).toContainElement(stakeField());
    expect(body).toContainElement(stakeChip("25"));
    expect(body).toContainElement(row("Balance"));
    expect(footer).toContainElement(button("Buy YES"));
    expect(body).not.toContainElement(button("Buy YES"));
    // On a short phone the body scrolls under the pinned button.
    expect(body).toHaveClass("overflow-y-auto");
    expect(ticketFrame()).toHaveClass("flex", "min-h-0", "flex-1", "flex-col");
  });
});

describe("opening the ticket", () => {
  test("reads the market and the caller's balance again: both may have changed between visits", async () => {
    const { api } = openTicket({
      routes: { [ME]: json(200, me({ balance_cr: "440" })) },
    });

    await waitFor(() => expect(api.calls(MARKET)).toHaveLength(1));
    await waitFor(() => expect(api.calls(ME)).toHaveLength(1));
  });

  test("the balance the top bar shows is then the one the API has now", async () => {
    const { client } = openTicket({
      routes: { [ME]: json(200, me({ balance_cr: "440" })) },
    });

    await waitFor(() =>
      expect(client.getQueryData<{ balance_cr: string }>(keys.me)?.balance_cr).toBe("440"),
    );
  });
});

describe("selling", () => {
  test("offers Half and All of the selected outcome, with the shares the server worked out", () => {
    openTicket({ market: withPosition(), start: SELL_YES });

    expect(sellChip("Half")).toHaveTextContent("45.82 shares");
    expect(sellChip("All")).toHaveTextContent("91.64 shares");
    expect(sellChip("Half")).toBeEnabled();
    expect(sellChip("Half")).toHaveAttribute("aria-pressed", "false");
    // Selling has no stake limit to show.
    expect(screen.queryByText(/left in this market/)).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Stake" })).not.toBeInTheDocument();
    expect(row("You receive")).toBeInTheDocument();
    expect(screen.queryByText("You pay")).not.toBeInTheDocument();
  });

  test("Confirm reads Sell YES shares and is the white primary button", () => {
    openTicket({ market: withPosition(), start: SELL_YES });

    const confirm = button("Sell YES shares");
    expect(confirm).toHaveClass("h-14", "bg-primary", "text-primary-foreground");
    expect(confirm).not.toHaveClass("bg-yes");
    expect(confirm).toBeDisabled();
  });

  test("offers an outcome only when one of its sell presets is allowed", () => {
    openTicket({ market: withPosition(), start: SELL_YES });

    expect(outcomeSegment("YES")).toBeEnabled();
    expect(outcomeSegment("NO")).toBeDisabled();
  });

  test("a preset the server does not allow is disabled", () => {
    const viewer = marketViewerWithPosition({
      sell_presets: [
        // Half of this holding is under the minimum trade; all of it is not.
        { outcome: "YES", portion: "HALF", shares: "0.75", allowed: false },
        { outcome: "YES", portion: "ALL", shares: "1.5", allowed: true },
        { outcome: "NO", portion: "HALF", shares: "0", allowed: false },
        { outcome: "NO", portion: "ALL", shares: "0", allowed: false },
      ],
    });
    openTicket({ market: marketDetail({ viewer }), start: SELL_YES });

    expect(sellChip("Half")).toBeDisabled();
    expect(sellChip("All")).toBeEnabled();
  });

  test("switching to Sell lands on an outcome that can be sold", async () => {
    const viewer = marketViewer({
      cost_cr: "30",
      holdings: holdings({}, { shares: "60.5", value_cr: "27.3" }),
      sell_presets: [
        { outcome: "YES", portion: "HALF", shares: "0", allowed: false },
        { outcome: "YES", portion: "ALL", shares: "0", allowed: false },
        { outcome: "NO", portion: "HALF", shares: "30.25", allowed: true },
        { outcome: "NO", portion: "ALL", shares: "60.5", allowed: true },
      ],
    });
    const { user, changes } = openTicket({ market: marketDetail({ viewer }) });

    await user.click(sideSegment("Sell"));

    expect(changes).toEqual([SELL_NO]);
    expect(button("Sell NO shares")).toBeInTheDocument();
    expect(sellChip("All")).toHaveTextContent("60.5 shares");
  });

  test("a portion is chosen for one outcome: changing the outcome asks to choose again", async () => {
    const viewer = marketViewerWithPosition({
      sell_presets: [
        { outcome: "YES", portion: "HALF", shares: "45.8225", allowed: true },
        { outcome: "YES", portion: "ALL", shares: "91.645", allowed: true },
        { outcome: "NO", portion: "HALF", shares: "10", allowed: true },
        { outcome: "NO", portion: "ALL", shares: "20", allowed: true },
      ],
    });
    const { user, api } = openTicket({
      market: marketDetail({ viewer }),
      start: SELL_YES,
      routes: { [QUOTE]: json(200, quote({ side: "SELL" })) },
    });

    await user.click(sellChip("All"));
    await waitFor(() => expect(button("Sell YES shares")).toBeEnabled());
    await user.click(outcomeSegment("NO"));

    expect(sellChip("All")).toHaveAttribute("aria-pressed", "false");
    expect(button("Sell NO shares")).toBeDisabled();
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(api.calls(QUOTE)).toHaveLength(1);
  });
});

describe("a viewer who cannot trade", () => {
  const paused = () =>
    marketDetail({
      status: "HALTED",
      trading_open: false,
      viewer: marketViewer({
        can_trade: false,
        cannot_trade_reason: "MARKET_NOT_OPEN",
        stake_presets: [
          { amount_cr: "25", allowed: false },
          { amount_cr: "50", allowed: false },
          { amount_cr: "100", allowed: false },
        ],
        buy_slider: null,
      }),
    });

  test("finds the stake entry switched off and Confirm disabled, and no quote is asked for", async () => {
    const { api } = openTicket({ market: paused() });

    for (const amount of ["25", "50", "100"]) expect(stakeChip(amount)).toBeDisabled();
    expect(stakeField()).toBeDisabled();
    expect(screen.queryByRole("slider")).not.toBeInTheDocument();
    expect(sideSegment("Sell")).toBeDisabled();
    expect(button("Buy YES")).toBeDisabled();

    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(api.calls(QUOTE)).toHaveLength(0);
  });

  test("the entry follows can_trade alone: a slider the server still sent is switched off with the rest", () => {
    openTicket({
      market: marketDetail({
        viewer: marketViewer({ can_trade: false, cannot_trade_reason: "OWN_MARKET" }),
      }),
    });

    expect(stakeField()).toBeDisabled();
    expect(document.querySelector("[data-slot=slider]")).toHaveAttribute("data-disabled");
    expect(button("Buy YES")).toBeDisabled();
  });

  test("when the market stops trading behind the ticket, the entry is switched off and its quote counts for nothing", async () => {
    const { user, api, client } = openTicket({ routes: { [QUOTE]: json(200, quote()) } });
    await user.click(stakeChip("25"));
    await waitFor(() => expect(button("Buy YES")).toBeEnabled());

    // A status event made Market detail read the market again: trading is paused.
    act(() => client.setQueryData(keys.markets.detail(MARKET_ID), paused()));

    await waitFor(() => expect(stakeChip("25")).toBeDisabled());
    // The amount is the user's and stays in the field; nothing can be done with it.
    expect(stakeField()).toHaveValue("25");
    expect(stakeField()).toBeDisabled();
    expect(button("Buy YES")).toBeDisabled();
    expect(within(row("You pay")).getByText("none yet")).toBeInTheDocument();
    // The quote that was read is not asked for again, and no trade goes out.
    await user.click(button("Buy YES"));
    expect(api.calls(QUOTE)).toHaveLength(1);
    expect(api.calls(TRADE)).toHaveLength(0);
  });
});

describe("a viewer who can buy nothing", () => {
  /** 100 of 100 tokens are in the market: no amount is a buy, and the holding can be sold. */
  const atTheLimit = () =>
    marketViewerWithPosition({
      cost_cr: "100",
      net_stake_cr: "100",
      stake_left_cr: "0",
      stake_presets: [
        { amount_cr: "25", allowed: false },
        { amount_cr: "50", allowed: false },
        { amount_cr: "100", allowed: false },
      ],
      buy_slider: null,
      can_buy: false,
    });

  test("at the stake limit the field takes no amount, the line says what is left, and no quote is asked for", async () => {
    const { user, api } = openTicket({ market: marketDetail({ viewer: atTheLimit() }) });

    expect(stakeField()).toBeDisabled();
    for (const amount of ["25", "50", "100"]) expect(stakeChip(amount)).toBeDisabled();
    expect(screen.queryByRole("slider")).not.toBeInTheDocument();
    expect(screen.getByText("0 of 100 tokens left in this market")).toBeInTheDocument();
    expect(button("Buy YES")).toBeDisabled();

    await user.type(stakeField(), "5");
    expect(stakeField()).toHaveValue("");
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(api.calls(QUOTE)).toHaveLength(0);
    expect(stakeRefusal()).not.toBeInTheDocument();
  });

  test("selling stays open, and its shares field takes a number", async () => {
    const { user } = openTicket({
      market: marketDetail({ viewer: atTheLimit() }),
      routes: { [QUOTE]: json(200, quote({ side: "SELL" })) },
    });

    expect(sideSegment("Sell")).toBeEnabled();
    await user.click(sideSegment("Sell"));

    expect(sharesField()).toBeEnabled();
    await user.type(sharesField(), "10");
    expect(sharesField()).toHaveValue("10");
  });

  test("the entry follows can_buy alone: a balance below the smallest buy switches it off as the limit does", () => {
    openTicket({
      market: marketDetail({
        viewer: marketViewer({ balance_cr: "0.5", buy_slider: null, can_buy: false }),
      }),
    });

    expect(stakeField()).toBeDisabled();
    expect(button("Buy YES")).toBeDisabled();
  });

  test("when the limit is reached behind the ticket, the amount stays in the field and is asked about no more", async () => {
    const { user, api, client } = openTicket({ routes: { [QUOTE]: json(200, quote()) } });
    await user.click(stakeChip("25"));
    await waitFor(() => expect(button("Buy YES")).toBeEnabled());

    // The market was read again: the viewer bought up to the limit somewhere else.
    act(() =>
      client.setQueryData(keys.markets.detail(MARKET_ID), marketDetail({ viewer: atTheLimit() })),
    );

    await waitFor(() => expect(stakeField()).toBeDisabled());
    expect(stakeField()).toHaveValue("25");
    expect(button("Buy YES")).toBeDisabled();
    expect(within(row("You pay")).getByText("none yet")).toBeInTheDocument();
    await user.click(button("Buy YES"));
    expect(api.calls(QUOTE)).toHaveLength(1);
    expect(api.calls(TRADE)).toHaveLength(0);
  });

  test("what a sale gives back can be bought again: the field takes an amount once can_buy is true", async () => {
    const { user, api, client } = openTicket({
      market: marketDetail({ viewer: atTheLimit() }),
      routes: { [QUOTE]: json(200, quote()) },
    });
    expect(stakeField()).toBeDisabled();
    // The read of the opening ticket has answered before the market changes.
    await waitFor(() => expect(api.calls(MARKET)).toHaveLength(1));
    await waitFor(() => expect(client.isFetching()).toBe(0));

    act(() =>
      client.setQueryData(
        keys.markets.detail(MARKET_ID),
        marketDetail({ viewer: marketViewerWithPosition() }),
      ),
    );

    await waitFor(() => expect(stakeField()).toBeEnabled());
    await user.type(stakeField(), "5");
    expect(stakeField()).toHaveValue("5");
  });
});

describe("the quote", () => {
  test("is asked for with the side, the outcome and the amount the field holds; skeleton bars, then its figures", async () => {
    const answer = deferred();
    const { user, api } = openTicket({ routes: { [QUOTE]: answer.handler } });

    await user.click(stakeChip("50"));
    expect(stakeField()).toHaveValue("50");
    expect(stakeChip("50")).toHaveAttribute("aria-pressed", "true");
    expect(stakeChip("50").className).toContain("data-[state=on]:bg-foreground");

    await waitFor(() => expect(api.calls(QUOTE)).toHaveLength(1));
    const asked = api.lastCall(QUOTE)!;
    expect(asked.path).toBe(`/api/markets/${MARKET_ID}/quote`);
    expect([...asked.query.entries()]).toEqual([
      ["side", "BUY"],
      ["outcome", "YES"],
      ["amount_cr", "50"],
    ]);
    expect(asked.headers.get("Accept")).toBe("application/json");

    // On its way: the labels stay put, the values are bars, and Confirm waits.
    for (const label of ["You pay", "Shares", "Average price", "New probability"]) {
      expect(row(label).querySelector("[data-slot=skeleton]")).toBeInTheDocument();
    }
    expect(document.querySelector("[data-slot=quote-panel] dl")).toHaveAttribute(
      "aria-busy",
      "true",
    );
    expect(button("Buy YES")).toBeDisabled();

    answer.resolve(
      json(
        200,
        quote({ amount_cr: "50", shares: "89.871234", average_price: 0.556, p_yes_after: 0.57 }),
      ),
    );

    await waitFor(() => expect(row("Shares")).toHaveTextContent("89.87"));
    expect(row("You pay")).toHaveTextContent("50 tokens");
    expect(row("Average price")).toHaveTextContent("0.556");
    expect(row("Average price")).toHaveTextContent("per share");
    expect(row("New probability")).toHaveTextContent("57.0% YES");
    expect(button("Buy YES")).toBeEnabled();
  });

  test("the panel shows the quote, not the input", async () => {
    const { user } = openTicket({
      routes: { [QUOTE]: json(200, quote({ amount_cr: "24.987654" })) },
    });

    await user.click(stakeChip("25"));

    await waitFor(() => expect(row("You pay")).toHaveTextContent("24.98 tokens"));
    expect(stakeChip("25")).toHaveAttribute("aria-pressed", "true");
  });

  test("when selling it is asked for with the preset's shares as they arrived, and no amount", async () => {
    const { user, api } = openTicket({
      market: withPosition(),
      start: SELL_YES,
      routes: {
        [QUOTE]: json(200, quote({ side: "SELL", shares: "45.8225", amount_cr: "23.874512" })),
      },
    });

    await user.click(sellChip("Half"));

    await waitFor(() => expect(button("Sell YES shares")).toBeEnabled());
    expect([...api.lastCall(QUOTE)!.query.entries()]).toEqual([
      ["side", "SELL"],
      ["outcome", "YES"],
      ["shares", "45.8225"],
    ]);
    expect(row("You receive")).toHaveTextContent("23.87 tokens");
    expect(sellChip("Half")).toHaveAttribute("aria-pressed", "true");
  });

  test("waits quote_debounce_ms after the last change, then asks once, for where the input ended", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const { user, api } = openTicket({
        routes: { [QUOTE]: json(200, quote({ outcome: "NO" })) },
        tunables: { quote_debounce_ms: 60_000 },
      });

      await user.click(stakeChip("25"));
      await user.click(stakeChip("50"));
      await user.click(outcomeSegment("NO"));
      await user.click(stakeChip("100"));

      await act(() => vi.advanceTimersByTimeAsync(30_000));
      expect(api.calls(QUOTE)).toHaveLength(0);
      // The input changed and its quote is not there yet: bars, and Confirm waits.
      expect(row("You pay").querySelector("[data-slot=skeleton]")).toBeInTheDocument();
      expect(button("Buy NO")).toBeDisabled();

      await act(() => vi.advanceTimersByTimeAsync(31_000));

      await waitFor(() => expect(button("Buy NO")).toBeEnabled());
      expect(api.calls(QUOTE)).toHaveLength(1);
      expect([...api.lastCall(QUOTE)!.query.entries()]).toEqual([
        ["side", "BUY"],
        ["outcome", "NO"],
        ["amount_cr", "100"],
      ]);
    } finally {
      vi.useRealTimers();
    }
  });

  test("Confirm is disabled again from a change until the quote for it has arrived", async () => {
    const { user, api } = openTicket({ routes: { [QUOTE]: json(200, quote()) } });
    await user.click(stakeChip("25"));
    await waitFor(() => expect(button("Buy YES")).toBeEnabled());

    const next = deferred();
    api.on(QUOTE, next.handler);
    await user.click(stakeChip("50"));

    expect(button("Buy YES")).toBeDisabled();
    await waitFor(() => expect(api.calls(QUOTE)).toHaveLength(2));
    expect(button("Buy YES")).toBeDisabled();

    next.resolve(json(200, quote({ amount_cr: "50", shares: "89.87" })));
    await waitFor(() => expect(button("Buy YES")).toBeEnabled());
    expect(row("Shares")).toHaveTextContent("89.87");
  });

  test("a quote for an earlier input never counts: while the debounce runs the panel waits and nothing can be confirmed", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      // No route for a trade: a request that went out with the wrong guard fails the test.
      const { user, api } = openTicket({
        routes: { [QUOTE]: json(200, quote()) },
        tunables: { quote_debounce_ms: 60_000 },
      });
      await user.click(stakeChip("25"));
      await act(() => vi.advanceTimersByTimeAsync(61_000));
      await waitFor(() => expect(button("Buy YES")).toBeEnabled());
      expect(row("Shares")).toHaveTextContent("45.12");

      // Another chip. What is on screen was quoted for 25, not for 50.
      await user.click(stakeChip("50"));

      expect(button("Buy YES")).toBeDisabled();
      expect(row("Shares")).not.toHaveTextContent("45.12");
      expect(row("Shares").querySelector("[data-slot=skeleton]")).toBeInTheDocument();
      await user.click(button("Buy YES"));
      expect(api.calls(TRADE)).toHaveLength(0);
      expect(api.calls(QUOTE)).toHaveLength(1);

      // Back on 25 before anything was asked: its quote is the quote for this input.
      await user.click(stakeChip("25"));

      expect(button("Buy YES")).toBeEnabled();
      expect(row("Shares")).toHaveTextContent("45.12");
      expect(api.calls(QUOTE)).toHaveLength(1);
    } finally {
      vi.useRealTimers();
    }
  });

  test("is not replaced by a live event: the user confirms what they were shown", async () => {
    const { user, api, client } = openTicket({ routes: { [QUOTE]: json(200, quote()) } });
    await user.click(stakeChip("25"));
    await waitFor(() => expect(row("Shares")).toHaveTextContent("45.12"));

    // What the market stream does with a quote event of another trader.
    act(() => {
      client.setQueryData<MarketDetailResponse>(keys.markets.detail(MARKET_ID), (market) =>
        market === undefined
          ? market
          : { ...market, p_yes: 0.61, p_no: 0.39, volume_cr: "1300", version: 9 },
      );
    });
    await new Promise((resolve) => setTimeout(resolve, 30));

    expect(api.calls(QUOTE)).toHaveLength(1);
    expect(row("Shares")).toHaveTextContent("45.12");
    expect(row("New probability")).toHaveTextContent("55.9% YES");
    expect(button("Buy YES")).toBeEnabled();
  });

  test("that cannot be read shows the API's message with Try again, which asks again", async () => {
    const { user, api } = openTicket({
      routes: {
        [QUOTE]: apiError(
          503,
          "SERVICE_UNAVAILABLE",
          "This is temporarily unavailable. Try again shortly.",
        ),
      },
    });

    await user.click(stakeChip("25"));

    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent("This is temporarily unavailable. Try again shortly.");
    expect(button("Buy YES")).toBeDisabled();
    expect(within(row("You pay")).getByText("none yet")).toBeInTheDocument();

    api.on(QUOTE, json(200, quote()));
    await user.click(button("Try again"));

    await waitFor(() => expect(button("Buy YES")).toBeEnabled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(api.calls(QUOTE)).toHaveLength(2);
  });

  test("answered RATE_LIMITED counts down the seconds the API names before it can be asked again", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const { user } = openTicket({
        routes: {
          [QUOTE]: apiError(429, "RATE_LIMITED", "Too many attempts. Try again later.", {
            retry_after_seconds: 30,
          }),
        },
      });

      await user.click(stakeChip("25"));

      expect(await screen.findByRole("alert")).toHaveTextContent(
        "Too many attempts. Try again later.",
      );
      expect(screen.getByRole("button", { name: /^Try again in \d+ s$/ })).toBeDisabled();

      await act(() => vi.advanceTimersByTimeAsync(31_000));

      expect(button("Try again")).toBeEnabled();
    } finally {
      vi.useRealTimers();
    }
  });

  describe("that says the market on screen is behind", () => {
    const BEHIND = [
      [409, "MARKET_NOT_OPEN", "This market is not open for trading."],
      [409, "INSUFFICIENT_SHARES", "You cannot sell more shares than you hold."],
      [403, "FORBIDDEN", "You proposed this market, so you cannot trade it."],
    ] as const;

    test.each(BEHIND)(
      "a %i %s stands above the button as a trade's refusal does, and the market is read again",
      async (status, code, message) => {
        const { user, api } = openTicket({ routes: { [QUOTE]: apiError(status, code, message) } });
        await waitFor(() => expect(api.calls(MARKET)).toHaveLength(1));

        await user.click(stakeChip("25"));

        const alert = await screen.findByRole("alert");
        expect(alert).toHaveTextContent(message);
        expect(alert).toHaveAttribute("data-slot", "inline-error");
        expect(alert).toHaveClass("text-no-text");
        const footer = ticketFrame()!.querySelector("[data-slot=sheet-footer]")!;
        expect(footer).toContainElement(alert);
        expect(alert.compareDocumentPosition(button("Buy YES")) & 4).toBeTruthy();
        // It is a verdict, not a failure: there is nothing to try again.
        expect(screen.queryByRole("button", { name: /Try again/ })).not.toBeInTheDocument();
        expect(button("Buy YES")).toBeDisabled();
        // Nothing is said about the amount: the stake entry is as it was.
        expect(stakeRefusal()).not.toBeInTheDocument();
        expect(stakeField()).not.toHaveAttribute("aria-invalid");

        await waitFor(() => expect(api.calls(MARKET)).toHaveLength(2));
        // Once, not again and again.
        await new Promise((resolve) => setTimeout(resolve, 30));
        expect(api.calls(MARKET)).toHaveLength(2);
        expect(api.calls(QUOTE)).toHaveLength(1);
      },
    );

    test("the message is still there once the market that was read again says the same", async () => {
      const closed = marketDetail({
        status: "CLOSED",
        trading_open: false,
        viewer: marketViewer({
          can_trade: false,
          cannot_trade_reason: "MARKET_NOT_OPEN",
          stake_presets: [
            { amount_cr: "25", allowed: false },
            { amount_cr: "50", allowed: false },
            { amount_cr: "100", allowed: false },
          ],
          buy_slider: null,
        }),
      });
      const { user, api } = openTicket({
        routes: {
          [QUOTE]: apiError(409, "MARKET_NOT_OPEN", "This market is not open for trading."),
        },
      });
      await waitFor(() => expect(api.calls(MARKET)).toHaveLength(1));
      api.on(MARKET, json(200, closed));

      await user.click(stakeChip("25"));

      // The sheet shows what can still be done: nothing, and why.
      await waitFor(() => expect(stakeField()).toBeDisabled());
      expect(screen.getByRole("alert")).toHaveTextContent("This market is not open for trading.");
      expect(screen.queryByRole("slider")).not.toBeInTheDocument();
      expect(button("Buy YES")).toBeDisabled();
    });

    test("when selling, a holding that changed is said above the button and the sell presets are read again", async () => {
      const after = marketDetail({
        viewer: marketViewerWithPosition({
          sell_presets: [
            { outcome: "YES", portion: "HALF", shares: "20", allowed: true },
            { outcome: "YES", portion: "ALL", shares: "40", allowed: true },
            { outcome: "NO", portion: "HALF", shares: "0", allowed: false },
            { outcome: "NO", portion: "ALL", shares: "0", allowed: false },
          ],
        }),
      });
      const { user, api } = openTicket({
        market: withPosition(),
        start: SELL_YES,
        routes: {
          [QUOTE]: apiError(
            409,
            "INSUFFICIENT_SHARES",
            "You cannot sell more shares than you hold.",
          ),
        },
      });
      await waitFor(() => expect(api.calls(MARKET)).toHaveLength(1));
      api.on(MARKET, json(200, after));

      await user.click(sellChip("All"));

      expect(await screen.findByRole("alert")).toHaveTextContent(
        "You cannot sell more shares than you hold.",
      );
      await waitFor(() => expect(sellChip("All")).toHaveTextContent("40 shares"));
      expect(button("Sell YES shares")).toBeDisabled();
    });
  });

  test("what a quote says about the size of a sale stands with the shares entry", async () => {
    const { user } = openTicket({
      market: withPosition(),
      start: SELL_YES,
      routes: {
        [QUOTE]: apiError(422, "TRADE_TOO_SMALL", "This trade is below the minimum size."),
      },
    });

    await user.click(sellChip("Half"));

    await waitFor(() => expect(sharesRefusal()).toBeInTheDocument());
    expect(sharesRefusal()).toHaveTextContent("This trade is below the minimum size.");
    expect(sharesField()).toHaveAttribute("aria-invalid", "true");
    // It is a verdict, not a failure: there is nothing to try again.
    expect(screen.queryByRole("button", { name: /Try again/ })).not.toBeInTheDocument();
    expect(button("Sell YES shares")).toBeDisabled();
  });
});

describe("the shares of a sell", () => {
  /** The latest quote that was asked for, as the parameters it went out with. */
  const asked = (api: ReturnType<typeof openTicket>["api"]) => [
    ...api.lastCall(QUOTE)!.query.entries(),
  ];
  const sale = quote({ side: "SELL", shares: "40", amount_cr: "20.5" });

  test("the slider runs where the server says, and writes the step it stands on into the field", async () => {
    const { user, api } = openTicket({
      market: withPosition(),
      start: SELL_YES,
      routes: { [QUOTE]: json(200, sale) },
    });

    // `slider` of the YES entry of `viewer.sell`: the whole shares of the holding.
    expect(sharesSlider()).toHaveAttribute("aria-valuemin", "1");
    expect(sharesSlider()).toHaveAttribute("aria-valuemax", "91");
    expect(sharesSlider()).toHaveAttribute("aria-valuetext", "not set");
    expect(sharesField()).toHaveValue("");

    sharesSlider().focus();
    await user.keyboard("{End}");

    expect(sharesField()).toHaveValue("91");
    expect(sharesSlider()).toHaveAttribute("aria-valuetext", "91 shares");
    await waitFor(() => expect(button("Sell YES shares")).toBeEnabled());
    expect(asked(api)).toEqual([
      ["side", "SELL"],
      ["outcome", "YES"],
      ["shares", "91"],
    ]);
  });

  test("a typed number of shares is quoted and sold with the characters that were typed", async () => {
    const { user, api } = openTicket({
      market: withPosition(),
      start: SELL_YES,
      routes: {
        [QUOTE]: json(200, quote({ side: "SELL", shares: "12.5", amount_cr: "6.4" })),
        [TRADE]: json(201, tradeResponse({ side: "SELL", shares: "12.5" })),
      },
    });

    await user.type(sharesField(), "12,5");

    await waitFor(() => expect(button("Sell YES shares")).toBeEnabled());
    expect(sharesField()).toHaveValue("12.5");
    expect(asked(api)).toEqual([
      ["side", "SELL"],
      ["outcome", "YES"],
      ["shares", "12.5"],
    ]);
    // No chip holds this number, and the slider cannot show it.
    expect(sellChip("Half")).toHaveAttribute("aria-pressed", "false");
    expect(sellChip("All")).toHaveAttribute("aria-pressed", "false");
    expect(sharesSlider()).toHaveAttribute("aria-valuetext", "not set");

    await user.click(button("Sell YES shares"));
    await screen.findByRole("heading", { name: "Sold 12.5 YES shares" });
    expect(api.lastCall(TRADE)!.rawBody).toBe(
      '{"side":"SELL","outcome":"YES","shares":"12.5","min_proceeds_cr":"6.4","quote_version":7}',
    );
  });

  test("a chip writes its shares into the field as they arrived", async () => {
    const { user } = openTicket({
      market: withPosition(),
      start: SELL_YES,
      routes: { [QUOTE]: json(200, sale) },
    });

    await user.click(sellChip("All"));

    expect(sharesField()).toHaveValue("91.645");
    expect(sellChip("All")).toHaveAttribute("aria-pressed", "true");
    expect(sellChip("Half")).toHaveAttribute("aria-pressed", "false");
  });

  test("more shares than the viewer holds is said with the shares entry, and the market is read again", async () => {
    const { user, api } = openTicket({
      market: withPosition(),
      start: SELL_YES,
      routes: {
        [QUOTE]: apiError(409, "INSUFFICIENT_SHARES", "You cannot sell more shares than you hold."),
      },
    });
    await waitFor(() => expect(api.calls(MARKET)).toHaveLength(1));

    await user.type(sharesField(), "500");

    await waitFor(() => expect(sharesRefusal()).toBeInTheDocument());
    expect(sharesRefusal()).toHaveTextContent("You cannot sell more shares than you hold.");
    expect(sharesField()).toHaveValue("500");
    expect(button("Sell YES shares")).toBeDisabled();
    await waitFor(() => expect(api.calls(MARKET)).toHaveLength(2));
    // Once, not again and again.
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(api.calls(MARKET)).toHaveLength(2);
  });

  test("an outcome without a slider has the field and the chips alone", () => {
    openTicket({
      market: marketDetail({
        viewer: marketViewerWithPosition({
          sell: [
            { outcome: "YES", slider: null },
            { outcome: "NO", slider: null },
          ],
        }),
      }),
      start: SELL_YES,
    });

    expect(screen.queryByRole("slider")).not.toBeInTheDocument();
    expect(sharesField()).toBeEnabled();
    expect(sellChip("All")).toBeEnabled();
  });
});

describe("the amount of a buy", () => {
  /** The latest quote that was asked for, as the parameters it went out with. */
  const asked = (api: ReturnType<typeof openTicket>["api"]) => [
    ...api.lastCall(QUOTE)!.query.entries(),
  ];

  test("is what the field holds: a typed amount is quoted with the characters that were typed", async () => {
    const { user, api } = openTicket({
      routes: { [QUOTE]: json(200, quote({ amount_cr: "37.5", shares: "67.4" })) },
    });

    await user.type(stakeField(), "37,5");

    await waitFor(() => expect(row("Shares")).toHaveTextContent("67.4"));
    expect(stakeField()).toHaveValue("37.5");
    expect(asked(api)).toEqual([
      ["side", "BUY"],
      ["outcome", "YES"],
      ["amount_cr", "37.5"],
    ]);
    expect(button("Buy YES")).toBeEnabled();
    // No preset holds this amount, and the slider cannot show it.
    for (const amount of ["25", "50", "100"]) {
      expect(stakeChip(amount)).toHaveAttribute("aria-pressed", "false");
    }
    expect(stakeSlider()).toHaveAttribute("aria-valuetext", "not set");
  });

  test.each(["007", "0.100000000000000000000000000001", "9007199254740993.000001"])(
    "the text %j goes out as it stands: nothing reads it as a number on the way",
    async (typed) => {
      const { user, api } = openTicket({ routes: { [QUOTE]: json(200, quote()) } });

      // Put in with one paste: the field takes it as it takes what is typed.
      await user.click(stakeField());
      await user.paste(typed);
      expect(stakeField()).toHaveValue(typed);

      await waitFor(() => expect(api.lastCall(QUOTE)?.query.get("amount_cr")).toBe(typed));
      await waitFor(() => expect(button("Buy YES")).toBeEnabled());
    },
  );

  test("moving the slider writes its step into the field, and that is what is quoted", async () => {
    const { user, api } = openTicket({ routes: { [QUOTE]: json(200, quote()) } });

    stakeSlider().focus();
    await user.keyboard("{End}");

    expect(stakeField()).toHaveValue("100");
    expect(stakeChip("100")).toHaveAttribute("aria-pressed", "true");
    await waitFor(() => expect(button("Buy YES")).toBeEnabled());
    expect(asked(api)).toEqual([
      ["side", "BUY"],
      ["outcome", "YES"],
      ["amount_cr", "100"],
    ]);
  });

  test("a tap on a preset writes its amount into the field, and the slider stands on it", async () => {
    const { user, api } = openTicket({ routes: { [QUOTE]: json(200, quote()) } });

    await user.click(stakeChip("25"));

    expect(stakeField()).toHaveValue("25");
    expect(stakeSlider()).toHaveAttribute("aria-valuenow", "25");
    expect(stakeSlider()).toHaveAttribute("aria-valuetext", "25 tokens");
    await waitFor(() => expect(api.calls(QUOTE)).toHaveLength(1));
    expect(api.lastCall(QUOTE)!.query.get("amount_cr")).toBe("25");
  });

  test("an empty field asks for nothing, and neither does one that ends in its decimal point", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const { user, api } = openTicket({
        routes: { [QUOTE]: json(200, quote({ amount_cr: "12.5" })) },
        tunables: { quote_debounce_ms: 60_000 },
      });

      await user.type(stakeField(), "12.");
      await act(() => vi.advanceTimersByTimeAsync(61_000));

      // That amount is not finished: nothing is on its way and nothing can be confirmed.
      expect(api.calls(QUOTE)).toHaveLength(0);
      expect(button("Buy YES")).toBeDisabled();
      expect(within(row("You pay")).getByText("none yet")).toBeInTheDocument();
      expect(row("You pay").querySelector("[data-slot=skeleton]")).not.toBeInTheDocument();

      await user.type(stakeField(), "5");
      await act(() => vi.advanceTimersByTimeAsync(61_000));

      await waitFor(() => expect(button("Buy YES")).toBeEnabled());
      expect(api.calls(QUOTE)).toHaveLength(1);
      expect(api.lastCall(QUOTE)!.query.get("amount_cr")).toBe("12.5");

      // Emptied again: the quote that was read counts for nothing.
      await user.clear(stakeField());
      await act(() => vi.advanceTimersByTimeAsync(61_000));

      expect(button("Buy YES")).toBeDisabled();
      expect(within(row("You pay")).getByText("none yet")).toBeInTheDocument();
      expect(api.calls(QUOTE)).toHaveLength(1);
    } finally {
      vi.useRealTimers();
    }
  });

  test("typing is one question once it has stopped, not one for every keystroke", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const { user, api } = openTicket({
        routes: { [QUOTE]: json(200, quote()) },
        tunables: { quote_debounce_ms: 60_000 },
      });

      await user.type(stakeField(), "1250");
      await act(() => vi.advanceTimersByTimeAsync(30_000));
      expect(api.calls(QUOTE)).toHaveLength(0);
      // On its way, as far as the screen knows: bars, and Confirm waits.
      expect(row("You pay").querySelector("[data-slot=skeleton]")).toBeInTheDocument();
      expect(button("Buy YES")).toBeDisabled();

      await act(() => vi.advanceTimersByTimeAsync(31_000));

      await waitFor(() => expect(button("Buy YES")).toBeEnabled());
      expect(api.calls(QUOTE)).toHaveLength(1);
      expect(api.lastCall(QUOTE)!.query.get("amount_cr")).toBe("1250");
    } finally {
      vi.useRealTimers();
    }
  });

  test("the frontend refuses no amount by itself: one above the balance, the stake that is left and the slider is asked like any other", async () => {
    const { user, api } = openTicket({
      market: marketDetail({
        viewer: marketViewer({
          balance_cr: "12",
          stake_left_cr: "30",
          buy_slider: { min: 1, max: 12, step: 1 },
        }),
      }),
      routes: { [QUOTE]: json(200, quote({ amount_cr: "5000" })) },
    });

    await user.type(stakeField(), "5000");

    await waitFor(() => expect(api.lastCall(QUOTE)?.query.get("amount_cr")).toBe("5000"));
    // The server answered with a quote, so it can be confirmed: its word, not ours.
    await waitFor(() => expect(button("Buy YES")).toBeEnabled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  test("is forgotten with the ticket, and kept when buying comes back from selling", async () => {
    const { user } = openTicket({
      market: withPosition(),
      routes: { [QUOTE]: json(200, quote()) },
    });
    await user.type(stakeField(), "37.5");

    await user.click(sideSegment("Sell"));
    expect(screen.queryByRole("textbox", { name: "Stake in tokens" })).not.toBeInTheDocument();
    await user.click(sideSegment("Buy"));

    expect(stakeField()).toHaveValue("37.5");

    await user.click(button("Close the sheet"));
    await new Promise((resolve) => setTimeout(resolve, 30));
    await user.click(button("Open on Buy YES"));

    expect(stakeField()).toHaveValue("");
  });

  test("another outcome is another quote for the same amount", async () => {
    const { user, api } = openTicket({ routes: { [QUOTE]: json(200, quote()) } });
    await user.type(stakeField(), "40");
    await waitFor(() => expect(button("Buy YES")).toBeEnabled());

    await user.click(outcomeSegment("NO"));

    expect(stakeField()).toHaveValue("40");
    await waitFor(() => expect(api.lastCall(QUOTE)?.query.get("outcome")).toBe("NO"));
    expect(api.lastCall(QUOTE)!.query.get("amount_cr")).toBe("40");
  });
});

describe("the server judges the amount", () => {
  const limit = apiError(409, "LIMIT_EXCEEDED", "This would exceed a limit.", {
    limit: "MAX_STAKE_PER_USER_MARKET",
    stake_limit_cr: "80",
    stake_left_cr: "12.5",
  });

  test("LIMIT_EXCEEDED stands with the stake entry: under the stake-left line, the field marked, no figures and no Confirm", async () => {
    const { user, api } = openTicket({ routes: { [QUOTE]: limit } });
    await waitFor(() => expect(api.calls(MARKET)).toHaveLength(1));

    await user.type(stakeField(), "150");

    const refusal = await screen.findByRole("alert");
    expect(refusal).toHaveTextContent("This would exceed a limit.");
    expect(stakeRefusal()).toBe(refusal);
    expect(refusal).toHaveClass("text-no-text");
    // Under the stake-left line, which says what the answer says is left.
    const stakeLeft = screen.getByText("12.5 of 80 tokens left in this market");
    expect(stakeLeft.compareDocumentPosition(refusal) & 4).toBeTruthy();
    expect(screen.queryByText("100 of 100 tokens left in this market")).not.toBeInTheDocument();
    expect(stakeField()).toHaveAttribute("aria-invalid", "true");
    expect(stakeField()).toHaveAccessibleDescription(
      "12.5 of 80 tokens left in this market This would exceed a limit.",
    );
    expect(stakeSlider()).toHaveAccessibleDescription(
      "12.5 of 80 tokens left in this market This would exceed a limit.",
    );

    // The quote panel shows no values and does not pulse.
    for (const label of ["You pay", "Shares", "Average price", "New probability"]) {
      expect(within(row(label)).getByText("none yet")).toBeInTheDocument();
      expect(row(label).querySelector("[data-slot=skeleton]")).not.toBeInTheDocument();
    }
    expect(button("Buy YES")).toBeDisabled();
    // It is the server's verdict on this amount: no banner, nothing to try again, and the
    // market on screen is not behind.
    expect(screen.queryByRole("button", { name: /Try again/ })).not.toBeInTheDocument();
    expect(ticketFrame()!.querySelector("[data-slot=sheet-footer] [role=alert]")).toBeNull();
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(api.calls(MARKET)).toHaveLength(1);
    expect(api.calls(TRADE)).toHaveLength(0);
  });

  test.each([
    [409, "INSUFFICIENT_BALANCE", "Not enough tokens for this."],
    [422, "TRADE_TOO_SMALL", "This trade is below the minimum size."],
    [422, "VALIDATION_ERROR", "The request is not valid."],
  ] as const)(
    "a %i %s stands with the stake entry, in the API's words",
    async (status, code, message) => {
      const { user } = openTicket({ routes: { [QUOTE]: apiError(status, code, message) } });

      await user.type(stakeField(), "0.5");

      const refusal = await screen.findByRole("alert");
      expect(refusal).toHaveTextContent(message);
      expect(stakeRefusal()).toBe(refusal);
      expect(stakeField()).toHaveAttribute("aria-invalid", "true");
      expect(button("Buy YES")).toBeDisabled();
      expect(within(row("You pay")).getByText("none yet")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Try again/ })).not.toBeInTheDocument();
      // The stake-left line is the market's own: this answer says nothing about it.
      expect(screen.getByText("100 of 100 tokens left in this market")).toBeInTheDocument();
    },
  );

  test("a text that is no amount is answered with the sentence the API wrote for the field", async () => {
    const { user } = openTicket({
      routes: {
        [QUOTE]: apiError(422, "VALIDATION_ERROR", "The request is not valid.", {
          errors: [
            {
              loc: ["query", "amount_cr"],
              msg: "Enter an amount with at most six decimal places.",
              type: "decimal_places",
            },
          ],
        }),
      },
    });

    await user.type(stakeField(), "1.0000001");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Enter an amount with at most six decimal places.",
    );
    expect(screen.queryByText("The request is not valid.")).not.toBeInTheDocument();
    expect(stakeRefusal()).toBeInTheDocument();
  });

  test("Confirm stays disabled until another amount has been quoted, and the refusal goes with the amount it was about", async () => {
    const { user, api } = openTicket({ routes: { [QUOTE]: limit } });
    await user.type(stakeField(), "150");
    await screen.findByRole("alert");

    api.on(QUOTE, json(200, quote({ amount_cr: "12" })));
    await user.clear(stakeField());

    // An empty field: the verdict was about 150, and there is nothing to judge.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(stakeField()).not.toHaveAttribute("aria-invalid");
    expect(button("Buy YES")).toBeDisabled();
    // The line is the market's own again.
    expect(screen.getByText("100 of 100 tokens left in this market")).toBeInTheDocument();

    await user.type(stakeField(), "12");

    await waitFor(() => expect(button("Buy YES")).toBeEnabled());
    expect(row("You pay")).toHaveTextContent("12 tokens");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  test("the same verdict for a preset: the amount is refused where it stands, and the chip stays the server's to disable", async () => {
    const { user } = openTicket({
      routes: { [QUOTE]: apiError(409, "INSUFFICIENT_BALANCE", "Not enough tokens for this.") },
    });

    await user.click(stakeChip("100"));

    expect(await screen.findByRole("alert")).toHaveTextContent("Not enough tokens for this.");
    expect(stakeRefusal()).toBeInTheDocument();
    // The market on screen still allows the preset: nothing here overrules that.
    expect(stakeChip("100")).toBeEnabled();
    expect(stakeChip("100")).toHaveAttribute("aria-pressed", "true");
  });

  test("a refusal of the trade itself stands above the button, not with the stake entry", async () => {
    const { user } = openTicket({
      routes: {
        [QUOTE]: json(200, quote()),
        [TRADE]: apiError(409, "INSUFFICIENT_BALANCE", "Not enough tokens for this."),
      },
    });
    await user.type(stakeField(), "25");
    await waitFor(() => expect(button("Buy YES")).toBeEnabled());

    await user.click(button("Buy YES"));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Not enough tokens for this.");
    expect(ticketFrame()!.querySelector("[data-slot=sheet-footer]")).toContainElement(alert);
    expect(stakeRefusal()).not.toBeInTheDocument();
    expect(stakeField()).not.toHaveAttribute("aria-invalid");
    expect(stakeField()).toHaveValue("25");
  });
});

describe("opening the ticket", () => {
  test("reads the market again, so the balance and the presets are current", async () => {
    const { user, api } = openTicket({ start: null });
    // Market detail read it when the screen opened.
    await waitFor(() => expect(api.calls(MARKET)).toHaveLength(1));

    api.on(
      MARKET,
      json(
        200,
        marketDetail({
          viewer: marketViewer({
            balance_cr: "440",
            stake_presets: [
              { amount_cr: "25", allowed: true },
              { amount_cr: "50", allowed: true },
              { amount_cr: "100", allowed: false },
            ],
          }),
        }),
      ),
    );
    await user.click(button("Open on Buy YES"));

    await waitFor(() => expect(api.calls(MARKET)).toHaveLength(2));
    await waitFor(() => expect(row("Balance")).toHaveTextContent("440 tokens"));
    expect(stakeChip("100")).toBeDisabled();
  });

  test("reads the range of the slider with it", async () => {
    const { user, api } = openTicket({ start: null });
    await waitFor(() => expect(api.calls(MARKET)).toHaveLength(1));

    api.on(
      MARKET,
      json(
        200,
        marketDetail({
          viewer: marketViewer({ stake_left_cr: "40", buy_slider: { min: 1, max: 40, step: 1 } }),
        }),
      ),
    );
    await user.click(button("Open on Buy YES"));

    await waitFor(() => expect(stakeSlider()).toHaveAttribute("aria-valuemax", "40"));
    expect(screen.getByText("40 of 100 tokens left in this market")).toBeInTheDocument();
  });
});

describe("the receipt", () => {
  async function buy(options: Parameters<typeof openTicket>[0] = {}) {
    const ticket = openTicket({
      ...options,
      routes: { [QUOTE]: json(200, quote()), ...options.routes },
    });
    await ticket.user.click(stakeChip("25"));
    await waitFor(() => expect(button("Buy YES")).toBeEnabled());
    await ticket.user.click(button("Buy YES"));
    return ticket;
  }

  test("takes the place of the form: what was bought, what was paid, the probability, the balance, and Done", async () => {
    await buy({
      routes: {
        [TRADE]: json(
          201,
          tradeResponse({
            shares: "45.123456",
            amount_cr: "24.999999",
            p_yes_after: 0.559,
            balance_cr: "215.000001",
          }),
        ),
      },
    });

    const headline = await screen.findByRole("heading", {
      name: "Bought 45.123456 YES shares",
    });
    expect(headline).toHaveClass("type-h2");
    expect(ticketFrame()).toHaveAttribute("data-view", "receipt");
    expect(ticketFrame()!.querySelector("svg.lucide-circle-check")).toBeInTheDocument();

    // A receipt is a detail view: the whole string, not the figure cut for a list.
    expect(within(row("You paid")).getByText("24.999999 tokens")).toBeInTheDocument();
    expect(row("New probability")).toHaveTextContent("55.9% YES");
    expect(within(row("Balance")).getByText("215.000001 tokens")).toBeInTheDocument();
    expect(button("Done")).toHaveClass("bg-primary", "w-full");

    // The form is gone.
    expect(screen.queryByRole("group", { name: "Stake" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Buy YES" })).not.toBeInTheDocument();
    expectInOrder([headline, row("You paid"), row("New probability"), row("Balance")]);
  });

  test("puts the focus on the result, and its token icon bounces once", async () => {
    await buy({ routes: { [TRADE]: json(201, tradeResponse()) } });

    expect(await screen.findByRole("heading", { name: /^Bought/ })).toHaveFocus();
    const bean = row("You paid").querySelector("[data-slot=token-icon]");
    expect(bean).toHaveClass("motion-safe:animate-bean-bounce");
    // One bean bounces, not every icon of the receipt.
    expect(row("Balance").querySelector("[data-slot=token-icon]")).not.toHaveClass(
      "motion-safe:animate-bean-bounce",
    );
  });

  test("of a sale says Sold and You received", async () => {
    const { user } = openTicket({
      market: withPosition(),
      start: SELL_YES,
      routes: {
        [QUOTE]: json(200, quote({ side: "SELL", shares: "45.8225", amount_cr: "23.874512" })),
        [TRADE]: json(
          201,
          tradeResponse({
            side: "SELL",
            shares: "45.8225",
            amount_cr: "23.874512",
            p_yes_after: 0.537,
            balance_cr: "213.874512",
          }),
        ),
      },
    });
    await user.click(sellChip("Half"));
    await waitFor(() => expect(button("Sell YES shares")).toBeEnabled());
    await user.click(button("Sell YES shares"));

    expect(
      await screen.findByRole("heading", { name: "Sold 45.8225 YES shares" }),
    ).toBeInTheDocument();
    expect(within(row("You received")).getByText("23.874512 tokens")).toBeInTheDocument();
    expect(row("New probability")).toHaveTextContent("53.7% YES");
    expect(within(row("Balance")).getByText("213.874512 tokens")).toBeInTheDocument();
  });

  test("Done tells the owner that the ticket is finished", async () => {
    const { user, changes } = await buy({ routes: { [TRADE]: json(201, tradeResponse()) } });

    await user.click(await screen.findByRole("button", { name: "Done" }));

    expect(changes).toEqual([null]);
    // The owner closed the sheet.
    expect(ticketFrame()).not.toBeInTheDocument();
  });

  test("in a sheet, Done leaves the receipt on it while it slides away, and the next opening is the form", async () => {
    const { user, changes } = await buy({
      surface: "closing-sheet",
      routes: { [TRADE]: json(201, tradeResponse()) },
    });

    await user.click(await screen.findByRole("button", { name: "Done" }));

    expect(changes).toEqual([null]);
    // Still on screen, on its way out: no form takes its place, so the sheet does not jump.
    expect(ticketFrame()).toHaveAttribute("data-view", "receipt");
    expect(screen.getByRole("heading", { name: /^Bought/ })).toBeInTheDocument();

    await user.click(button("Close the sheet"));
    await new Promise((resolve) => setTimeout(resolve, 30));
    await user.click(button("Open on Buy YES"));

    expect(ticketFrame()).toHaveAttribute("data-view", "form");
    expect(stakeChip("25")).toHaveAttribute("aria-pressed", "false");
    expect(button("Buy YES")).toBeDisabled();
  });

  test("in the real sheet, Done closes it and the next opening is the form", async () => {
    const { user, changes } = await buy({
      surface: "sheet",
      routes: { [TRADE]: json(201, tradeResponse()) },
    });

    await user.click(await screen.findByRole("button", { name: "Done" }));

    expect(changes).toEqual([null]);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await new Promise((resolve) => setTimeout(resolve, 30));
    await user.click(button("Open on Buy YES"));

    await waitFor(() => expect(ticketFrame()).toHaveAttribute("data-view", "form"));
    expect(stakeChip("25")).toHaveAttribute("aria-pressed", "false");
  });

  test("where the ticket stays, Done brings back an empty form on what the answer made of the market", async () => {
    const after = marketDetail({
      p_yes: 0.559,
      p_no: 0.441,
      version: 8,
      viewer: marketViewer({
        balance_cr: "215",
        cost_cr: "25",
        net_stake_cr: "25",
        stake_left_cr: "75",
      }),
    });
    const { user, api, changes, client } = openTicket({
      surface: "panel",
      routes: { [QUOTE]: json(200, quote()) },
    });
    await user.click(stakeChip("25"));
    await waitFor(() => expect(button("Buy YES")).toBeEnabled());
    api.on(MARKET, json(200, after));
    api.on(ME, json(200, me({ balance_cr: "215" })));
    api.on(TRADE, json(201, tradeResponse()));
    await user.click(button("Buy YES"));

    await user.click(await screen.findByRole("button", { name: "Done" }));

    expect(changes).toEqual([null]);
    expect(ticketFrame()).toHaveAttribute("data-view", "form");
    expect(ticketFrame()).toHaveFocus();
    expect(stakeChip("25")).toHaveAttribute("aria-pressed", "false");
    expect(button("Buy YES")).toBeDisabled();
    expect(within(row("You pay")).getByText("none yet")).toBeInTheDocument();
    // The market behind the receipt was updated from the trade's answer and read again.
    expect(row("Balance")).toHaveTextContent("215 tokens");
    expect(screen.getByText("75 of 100 tokens left in this market")).toBeInTheDocument();
    expect(client.getQueryData<{ balance_cr: string }>(keys.me)?.balance_cr).toBe("215");
  });
});

describe("amounts are strings from the input to the request to the receipt", () => {
  // More digits than a float keeps. Any detour through a number would change them.
  const held = "9007199254740993.000000000000000001";
  const proceeds = "4503599627370496.100000000000000005";
  const balance = "4503599627370736.100000000000000005";

  test("a sale sends the preset's shares and the quote's amount untouched, and the receipt shows the answer's", async () => {
    const viewer = marketViewerWithPosition({
      sell_presets: [
        { outcome: "YES", portion: "HALF", shares: "1", allowed: true },
        { outcome: "YES", portion: "ALL", shares: held, allowed: true },
        { outcome: "NO", portion: "HALF", shares: "0", allowed: false },
        { outcome: "NO", portion: "ALL", shares: "0", allowed: false },
      ],
    });
    const { user, api } = openTicket({
      market: marketDetail({ viewer }),
      start: SELL_YES,
      routes: {
        [QUOTE]: json(200, quote({ side: "SELL", shares: held, amount_cr: proceeds })),
        [TRADE]: json(
          201,
          tradeResponse({ side: "SELL", shares: held, amount_cr: proceeds, balance_cr: balance }),
        ),
      },
    });

    await user.click(sellChip("All"));
    await waitFor(() => expect(button("Sell YES shares")).toBeEnabled());
    expect(api.lastCall(QUOTE)!.query.get("shares")).toBe(held);

    await user.click(button("Sell YES shares"));
    await screen.findByRole("heading", {
      name: "Sold 9,007,199,254,740,993.000000000000000001 YES shares",
    });

    expect(api.lastCall(TRADE)!.rawBody).toBe(
      `{"side":"SELL","outcome":"YES","shares":"${held}","min_proceeds_cr":"${proceeds}","quote_version":7}`,
    );
    expect(
      within(row("You received")).getByText("4,503,599,627,370,496.100000000000000005 tokens"),
    ).toBeInTheDocument();
    expect(
      within(row("Balance")).getByText("4,503,599,627,370,736.100000000000000005 tokens"),
    ).toBeInTheDocument();
  });

  test("a buy sends the preset's amount and the quote's shares untouched", async () => {
    const stake = "0.100000000000000000000000000001";
    const shares = "0.179999999999999999999999999999";
    const viewer = marketViewer({ stake_presets: [{ amount_cr: stake, allowed: true }] });
    const { user, api } = openTicket({
      market: marketDetail({ viewer }),
      routes: {
        [QUOTE]: json(200, quote({ amount_cr: stake, shares, quote_version: 12 })),
        [TRADE]: deferred().handler,
      },
    });

    await user.click(
      within(screen.getByRole("group", { name: "Quick amounts" })).getByRole("button"),
    );
    expect(stakeField()).toHaveValue(stake);
    await waitFor(() => expect(button("Buy YES")).toBeEnabled());
    expect(api.lastCall(QUOTE)!.query.get("amount_cr")).toBe(stake);
    await user.click(button("Buy YES"));

    await waitFor(() => expect(api.calls(TRADE)).toHaveLength(1));
    expect(api.lastCall(TRADE)!.rawBody).toBe(
      `{"side":"BUY","outcome":"YES","amount_cr":"${stake}","min_shares":"${shares}","quote_version":12}`,
    );
  });

  test("a buy sends the amount as it was typed, not as the quote answered it", async () => {
    // Zeros a number would drop, and more digits than a float keeps.
    const typed = "025.500000000000000000000000000001";
    const shares = "45.999999999999999999999999999999";
    const { user, api } = openTicket({
      routes: {
        // The server's own writing of the amount: the request keeps the user's.
        [QUOTE]: json(200, quote({ amount_cr: "25.5", shares, quote_version: 12 })),
        [TRADE]: deferred().handler,
      },
    });

    // Put in with one paste: the field takes it as it takes what is typed.
    await user.click(stakeField());
    await user.paste(typed);
    expect(stakeField()).toHaveValue(typed);
    await waitFor(() => expect(button("Buy YES")).toBeEnabled());
    expect(api.lastCall(QUOTE)!.query.get("amount_cr")).toBe(typed);
    expect(row("You pay")).toHaveTextContent("25.5 tokens");
    await user.click(button("Buy YES"));

    await waitFor(() => expect(api.calls(TRADE)).toHaveLength(1));
    expect(api.lastCall(TRADE)!.rawBody).toBe(
      `{"side":"BUY","outcome":"YES","amount_cr":"${typed}","min_shares":"${shares}","quote_version":12}`,
    );
  });

  test("a buy from the slider sends the digits of its step", async () => {
    const { user, api } = openTicket({
      routes: { [QUOTE]: json(200, quote({ amount_cr: "2" })), [TRADE]: deferred().handler },
    });

    stakeSlider().focus();
    await user.keyboard("{ArrowRight}");
    await waitFor(() => expect(button("Buy YES")).toBeEnabled());
    await user.click(button("Buy YES"));

    await waitFor(() => expect(api.calls(TRADE)).toHaveLength(1));
    expect(api.lastCall(TRADE)!.rawBody).toBe(
      '{"side":"BUY","outcome":"YES","amount_cr":"2","min_shares":"45.12","quote_version":7}',
    );
  });
});

describe("how the market is priced is VIS's business", () => {
  test("the ticket names no tier, no liquidity figure and no loss bound, also when the API sends them", async () => {
    // An admin reads the market with its tier and the two figures of it.
    const market = adminMarketDetail({ b_cr: "1000", max_loss_cr: "693.147181" });
    const { user } = openTicket({ market, routes: { [QUOTE]: json(200, quote()) } });
    await user.click(stakeChip("25"));
    await waitFor(() => expect(button("Buy YES")).toBeEnabled());

    const text = ticketFrame()!.textContent ?? "";
    expect(text).not.toMatch(/tier|liquidity|loss|budget|default/i);
    expect(text).not.toContain("1,000");
    expect(text).not.toContain("693");
  });
});
