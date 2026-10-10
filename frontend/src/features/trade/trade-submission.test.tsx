import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { page } from "@/lib/browser";
import {
  MARKET_ID,
  marketDetail,
  marketViewer,
  marketViewerWithPosition,
  quote,
  tradeResponse,
} from "@/test/fixtures";
import {
  apiError,
  deferred,
  errorPage,
  type Handler,
  json,
  noAnswer,
  portal401,
} from "@/test/http";
import { setViewport } from "@/test/media";

import {
  button,
  MARKET,
  openTicket,
  outcomeSegment,
  QUOTE,
  row,
  SELL_YES,
  sellChip,
  sent,
  sideSegment,
  stakeChip,
  type TicketOptions,
  ticketFrame,
  TRADE,
} from "./test-harness";

/**
 * DESIGN.md §14, "A submitted trade", through the ticket: one Idempotency-Key and one
 * serialised body per attempt, kept until a 2xx or a 4xx other than 429.
 */

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const BUY_25 =
  '{"side":"BUY","outcome":"YES","amount_cr":"25","min_shares":"45.12","quote_version":7}';

/** Opens the ticket on Buy YES, chooses 25, waits for the quote and taps Confirm. */
async function confirmBuy(options: TicketOptions = {}) {
  const ticket = openTicket({
    ...options,
    routes: { [QUOTE]: json(200, quote()), ...options.routes },
  });
  await ticket.user.click(stakeChip("25"));
  await waitFor(() => expect(button("Buy YES")).toBeEnabled());
  await ticket.user.click(button("Buy YES"));
  return ticket;
}

/** Every trade request so far: its key and its body, as they went out. */
const wire = (api: ReturnType<typeof openTicket>["api"]) => api.calls(TRADE).map(sent);

beforeEach(() => {
  // The portal's 401 reloads the page; a test stays where it is.
  vi.spyOn(page, "reload").mockImplementation(() => {});
});

describe("Confirm", () => {
  test("sends the chip as it arrived with the quote as its guard, under one Idempotency-Key", async () => {
    const { api } = await confirmBuy({ routes: { [TRADE]: deferred().handler } });

    await waitFor(() => expect(api.calls(TRADE)).toHaveLength(1));
    const request = api.lastCall(TRADE)!;
    expect(request.path).toBe(`/api/markets/${MARKET_ID}/trades`);
    expect(request.rawBody).toBe(BUY_25);
    expect(request.headers.get("Idempotency-Key")).toMatch(UUID);
    expect(request.headers.get("Content-Type")).toBe("application/json");
    expect(request.headers.get("Accept")).toBe("application/json");
  });

  test("shows its spinner and disables the whole form until the answer arrives", async () => {
    const answer = deferred();
    await confirmBuy({
      market: marketDetail({ viewer: marketViewerWithPosition() }),
      routes: { [TRADE]: answer.handler },
    });

    expect(button("Buy YES")).toBeDisabled();
    expect(button("Buy YES")).toHaveAttribute("aria-busy", "true");
    expect(sideSegment("Buy")).toBeDisabled();
    expect(sideSegment("Sell")).toBeDisabled();
    expect(outcomeSegment("YES")).toBeDisabled();
    expect(outcomeSegment("NO")).toBeDisabled();
    for (const amount of ["25", "50", "100"]) expect(stakeChip(amount)).toBeDisabled();
    // What is being sent stays on screen: the chip, and the quote it was confirmed with.
    expect(stakeChip("25")).toHaveAttribute("aria-pressed", "true");
    expect(row("Shares")).toHaveTextContent("45.12");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    answer.resolve(json(201, tradeResponse()));
    expect(await screen.findByRole("heading", { name: /^Bought/ })).toBeInTheDocument();
  });

  test("tapped twice is one request", async () => {
    const { user, api } = await confirmBuy({ routes: { [TRADE]: deferred().handler } });

    await user.click(button("Buy YES"));
    await user.dblClick(button("Buy YES"));

    await waitFor(() => expect(api.calls(TRADE)).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(api.calls(TRADE)).toHaveLength(1);
  });

  test("tapped twice before the screen has drawn the spinner is still one request", async () => {
    const { user, api } = openTicket({
      routes: { [QUOTE]: json(200, quote()), [TRADE]: deferred().handler },
    });
    await user.click(stakeChip("25"));
    await waitFor(() => expect(button("Buy YES")).toBeEnabled());
    const confirm = button("Buy YES");

    // Both clicks reach the button as it was drawn: enabled, without a spinner.
    act(() => {
      confirm.click();
      confirm.click();
    });

    await waitFor(() => expect(api.calls(TRADE)).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(api.calls(TRADE)).toHaveLength(1);
  });
});

/** Outcomes that say nothing about the trade: the body and its key are kept. */
const NO_VERDICT: [name: string, answer: Handler, message: string][] = [
  [
    "a 503 with the API's code",
    apiError(503, "SERVICE_UNAVAILABLE", "This is temporarily unavailable. Try again shortly."),
    "This is temporarily unavailable. Try again shortly.",
  ],
  ["a 500", apiError(500, "INTERNAL_ERROR", "Something went wrong."), "Something went wrong."],
  [
    "an error page of the proxy",
    errorPage(502),
    "This is temporarily unavailable. Try again shortly.",
  ],
  ["the portal's 401", portal401(), "Your ETH sign-in has ended."],
  ["a network error", noAnswer, "No connection. Try again."],
];

describe.each(NO_VERDICT)("after %s", (_name, answer, message) => {
  test("the message is shown above the button, and the sheet offers nothing but Try again", async () => {
    const { api } = await confirmBuy({
      market: marketDetail({ viewer: marketViewerWithPosition() }),
      routes: { [TRADE]: answer },
    });

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(message);
    expect(alert).toHaveClass("text-no-text");

    const retry = button("Try again");
    expect(retry).toBeEnabled();
    expect(retry).toHaveClass("h-14", "bg-yes");
    expect(alert.compareDocumentPosition(retry) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Buy YES" })).not.toBeInTheDocument();

    expect(sideSegment("Buy")).toBeDisabled();
    expect(sideSegment("Sell")).toBeDisabled();
    expect(outcomeSegment("YES")).toBeDisabled();
    expect(outcomeSegment("NO")).toBeDisabled();
    for (const amount of ["25", "50", "100"]) expect(stakeChip(amount)).toBeDisabled();
    expect(stakeChip("25")).toHaveAttribute("aria-pressed", "true");
    expect(row("Shares")).toHaveTextContent("45.12");

    // Nothing is sent again without a tap.
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(api.calls(TRADE)).toHaveLength(1);
  });

  test("Try again sends the stored body with the stored key, unchanged", async () => {
    const { user, api } = await confirmBuy({ routes: { [TRADE]: answer } });
    await screen.findByRole("alert");

    // A later quote must not find its way into the request.
    api.on(QUOTE, json(200, quote({ shares: "40", quote_version: 11 })));
    const replay = deferred();
    api.on(TRADE, replay.handler);
    await user.click(button("Try again"));

    await waitFor(() => expect(api.calls(TRADE)).toHaveLength(2));
    const [first, second] = wire(api);
    expect(first).toEqual({ key: expect.stringMatching(UUID), body: BUY_25 });
    expect(second).toEqual(first);
    // On its way again: the spinner, and the message of the earlier try is gone.
    expect(button("Try again")).toHaveAttribute("aria-busy", "true");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    replay.resolve(json(201, tradeResponse()));
    expect(
      await screen.findByRole("heading", { name: "Bought 45.123456 YES shares" }),
    ).toBeInTheDocument();
  });
});

describe("a 429", () => {
  const limited = (seconds: number) =>
    apiError(429, "RATE_LIMITED", "Too many attempts. Try again later.", {
      retry_after_seconds: seconds,
    });

  test("keeps the body and its key, and counts down retry_after_seconds before Try again", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const { user, api } = await confirmBuy({ routes: { [TRADE]: limited(30) } });

      expect(await screen.findByRole("alert")).toHaveTextContent(
        "Too many attempts. Try again later.",
      );
      const countdown = document.querySelector("[data-slot=retry-countdown]");
      // A slow machine may be a few seconds in already; the count never goes up.
      expect(countdown).toHaveTextContent(/^Try again in (30|2\d) s$/);
      expect(button("Try again")).toBeDisabled();

      await act(() => vi.advanceTimersByTimeAsync(10_000));
      expect(document.querySelector("[data-slot=retry-countdown]")).toHaveTextContent(
        /^Try again in (20|1\d) s$/,
      );
      expect(button("Try again")).toBeDisabled();

      await act(() => vi.advanceTimersByTimeAsync(21_000));
      expect(document.querySelector("[data-slot=retry-countdown]")).not.toBeInTheDocument();
      expect(button("Try again")).toBeEnabled();

      api.on(TRADE, json(201, tradeResponse()));
      await user.click(button("Try again"));
      await screen.findByRole("heading", { name: /^Bought/ });

      const [first, second] = wire(api);
      expect(wire(api)).toHaveLength(2);
      expect(second).toEqual(first);
    } finally {
      vi.useRealTimers();
    }
  });

  test("after an unanswered attempt is one more replay of the same body with the same key", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const { user, api } = await confirmBuy({ routes: { [TRADE]: noAnswer } });
      expect(await screen.findByRole("alert")).toHaveTextContent("No connection. Try again.");

      api.on(TRADE, limited(30));
      await user.click(button("Try again"));
      await waitFor(() =>
        expect(screen.getByRole("alert")).toHaveTextContent("Too many attempts. Try again later."),
      );
      expect(button("Try again")).toBeDisabled();

      await act(() => vi.advanceTimersByTimeAsync(31_000));
      api.on(TRADE, json(201, tradeResponse()));
      await user.click(button("Try again"));
      await screen.findByRole("heading", { name: /^Bought/ });

      const requests = wire(api);
      expect(requests).toHaveLength(3);
      expect(requests[0]).toEqual({ key: expect.stringMatching(UUID), body: BUY_25 });
      expect(requests[1]).toEqual(requests[0]);
      expect(requests[2]).toEqual(requests[0]);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("an unanswered trade survives the sheet", () => {
  test("closing and reopening shows the same trade, whatever the sheet is opened for, and sends nothing", async () => {
    const { user, api } = await confirmBuy({
      market: marketDetail({ viewer: marketViewerWithPosition() }),
      routes: { [TRADE]: noAnswer },
    });
    await screen.findByRole("alert");

    await user.click(button("Close the sheet"));
    expect(ticketFrame()).not.toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 30));
    await user.click(button("Open on Sell YES"));

    // Not the sale it was opened for: the buy that has no answer yet.
    expect(ticketFrame()).toHaveAttribute("data-side", "BUY");
    expect(ticketFrame()).toHaveAttribute("data-outcome", "YES");
    expect(sideSegment("Buy")).toHaveAttribute("aria-checked", "true");
    expect(outcomeSegment("YES")).toHaveAttribute("aria-pressed", "true");
    expect(stakeChip("25")).toHaveAttribute("aria-pressed", "true");
    for (const control of [
      sideSegment("Buy"),
      sideSegment("Sell"),
      outcomeSegment("YES"),
      outcomeSegment("NO"),
      stakeChip("25"),
      stakeChip("50"),
    ]) {
      expect(control).toBeDisabled();
    }
    expect(row("Shares")).toHaveTextContent("45.12");
    expect(screen.getByRole("alert")).toHaveTextContent("No connection. Try again.");
    expect(screen.queryByRole("button", { name: /^(Buy|Sell) / })).not.toBeInTheDocument();

    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(api.calls(TRADE)).toHaveLength(1);

    api.on(TRADE, json(201, tradeResponse()));
    await user.click(button("Try again"));
    await screen.findByRole("heading", { name: /^Bought/ });

    const [first, second] = wire(api);
    expect(wire(api)).toHaveLength(2);
    expect(second).toEqual(first);
  });

  test("a trade that fails after the sheet was closed is there at the next opening", async () => {
    const answer = deferred();
    const { user, api } = await confirmBuy({ routes: { [TRADE]: answer.handler } });
    await waitFor(() => expect(api.calls(TRADE)).toHaveLength(1));

    await user.click(button("Close the sheet"));
    answer.resolve(errorPage(504));
    await new Promise((resolve) => setTimeout(resolve, 30));
    await user.click(button("Open on Buy NO"));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This is temporarily unavailable. Try again shortly.",
    );
    expect(ticketFrame()).toHaveAttribute("data-outcome", "YES");
    api.on(TRADE, deferred().handler);
    await user.click(button("Try again"));

    await waitFor(() => expect(api.calls(TRADE)).toHaveLength(2));
    expect(wire(api)[1]).toEqual(wire(api)[0]);
  });

  test("keeps the stakes it was made from, also when the market offers others by now", async () => {
    const { user, api } = await confirmBuy({ routes: { [TRADE]: noAnswer } });
    await screen.findByRole("alert");
    await user.click(button("Close the sheet"));
    await new Promise((resolve) => setTimeout(resolve, 30));

    api.on(
      MARKET,
      json(
        200,
        marketDetail({
          viewer: marketViewer({
            balance_cr: "215",
            stake_presets: [
              { amount_cr: "10", allowed: true },
              { amount_cr: "20", allowed: true },
              { amount_cr: "40", allowed: true },
            ],
          }),
        }),
      ),
    );
    await user.click(button("Open on Buy YES"));
    await waitFor(() => expect(row("Balance")).toHaveTextContent("215 tokens"));

    expect(stakeChip("25")).toHaveAttribute("aria-pressed", "true");
    expect(stakeChip("25")).toBeDisabled();
    expect(stakeChip("50")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "10 tokens" })).not.toBeInTheDocument();
    expect(button("Try again")).toBeEnabled();
  });

  test("is shown as it was sent, also when the market and the price have moved on meanwhile", async () => {
    const sale = quote({ side: "SELL", shares: "45.8225", amount_cr: "23.874512" });
    const { user, api } = openTicket({
      market: marketDetail({ viewer: marketViewerWithPosition() }),
      start: SELL_YES,
      routes: { [QUOTE]: json(200, sale), [TRADE]: noAnswer },
    });
    await user.click(sellChip("Half"));
    await waitFor(() => expect(button("Sell YES shares")).toBeEnabled());
    await user.click(button("Sell YES shares"));
    await screen.findByRole("alert");
    await user.click(button("Close the sheet"));
    await new Promise((resolve) => setTimeout(resolve, 30));

    // The sale went through on the server, though its answer was lost: the holding is
    // smaller now, and a quote asked today would be another one.
    api.on(
      MARKET,
      json(
        200,
        marketDetail({
          viewer: marketViewerWithPosition({
            balance_cr: "213.874512",
            sell_presets: [
              { outcome: "YES", portion: "HALF", shares: "22.91125", allowed: true },
              { outcome: "YES", portion: "ALL", shares: "45.8225", allowed: true },
              { outcome: "NO", portion: "HALF", shares: "0", allowed: false },
              { outcome: "NO", portion: "ALL", shares: "0", allowed: false },
            ],
          }),
        }),
      ),
    );
    api.on(QUOTE, apiError(503, "SERVICE_UNAVAILABLE", "This is temporarily unavailable."));
    await user.click(button("Open on Buy YES"));
    await waitFor(() => expect(row("Balance")).toHaveTextContent("213.87 tokens"));

    // The chips and the quote of the trade that waits, not those of today.
    expect(sellChip("Half")).toHaveTextContent("45.82 shares");
    expect(sellChip("Half")).toHaveAttribute("aria-pressed", "true");
    expect(sellChip("All")).toHaveTextContent("91.64 shares");
    expect(sellChip("Half")).toBeDisabled();
    expect(sellChip("All")).toBeDisabled();
    expect(row("You receive")).toHaveTextContent("23.87 tokens");
    expect(row("Shares")).toHaveTextContent("45.82");
    // One message: why the trade has no answer. A quote that cannot be read is no news here.
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(screen.getByRole("alert")).toHaveTextContent("No connection. Try again.");

    api.on(TRADE, json(201, tradeResponse({ side: "SELL", shares: "45.8225" })));
    await user.click(button("Try again"));
    await screen.findByRole("heading", { name: "Sold 45.8225 YES shares" });

    const [first, second] = wire(api);
    expect(first?.body).toBe(
      '{"side":"SELL","outcome":"YES","shares":"45.8225","min_proceeds_cr":"23.874512","quote_version":7}',
    );
    expect(second).toEqual(first);
  });

  test("a sheet reopened while the trade is still on its way shows it on its way", async () => {
    const answer = deferred();
    const { user, api } = await confirmBuy({ routes: { [TRADE]: answer.handler } });
    await waitFor(() => expect(api.calls(TRADE)).toHaveLength(1));

    await user.click(button("Close the sheet"));
    await user.click(button("Open on Buy YES"));

    expect(button("Buy YES")).toHaveAttribute("aria-busy", "true");
    expect(stakeChip("25")).toBeDisabled();

    answer.resolve(json(201, tradeResponse()));
    expect(await screen.findByRole("heading", { name: /^Bought/ })).toBeInTheDocument();
    expect(api.calls(TRADE)).toHaveLength(1);
  });

  test("a trade executed after the sheet was closed is confirmed with a toast, and the next opening is an empty form", async () => {
    const answer = deferred();
    const { user, api } = await confirmBuy({ routes: { [TRADE]: answer.handler } });
    await waitFor(() => expect(api.calls(TRADE)).toHaveLength(1));

    await user.click(button("Close the sheet"));
    await new Promise((resolve) => setTimeout(resolve, 30));
    answer.resolve(json(201, tradeResponse()));

    expect(await screen.findByText("Bought 45.123456 YES shares")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /^Bought/ })).not.toBeInTheDocument();

    await user.click(button("Open on Buy YES"));
    expect(ticketFrame()).toHaveAttribute("data-view", "form");
    expect(stakeChip("25")).toHaveAttribute("aria-pressed", "false");
    expect(button("Buy YES")).toBeDisabled();
  });

  test("a trade refused after the sheet was closed leaves the form as it was, with the message", async () => {
    const answer = deferred();
    const { user, api } = await confirmBuy({ routes: { [TRADE]: answer.handler } });
    await waitFor(() => expect(api.calls(TRADE)).toHaveLength(1));

    await user.click(button("Close the sheet"));
    await new Promise((resolve) => setTimeout(resolve, 30));
    answer.resolve(apiError(409, "INSUFFICIENT_BALANCE", "Not enough tokens for this."));
    await new Promise((resolve) => setTimeout(resolve, 30));
    await user.click(button("Open on Buy YES"));

    expect(await screen.findByRole("alert")).toHaveTextContent("Not enough tokens for this.");
    expect(stakeChip("25")).toHaveAttribute("aria-pressed", "true");
    expect(stakeChip("25")).toBeEnabled();

    // Read, then closed: the next opening starts empty.
    await user.click(button("Close the sheet"));
    await new Promise((resolve) => setTimeout(resolve, 30));
    await user.click(button("Open on Buy YES"));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(stakeChip("25")).toHaveAttribute("aria-pressed", "false");
  });

  test("a form that was closed without a trade starts empty", async () => {
    const { user, api } = openTicket({ routes: { [QUOTE]: json(200, quote()) } });
    await user.click(stakeChip("50"));
    await waitFor(() => expect(button("Buy YES")).toBeEnabled());

    await user.click(button("Close the sheet"));
    await new Promise((resolve) => setTimeout(resolve, 30));
    await user.click(button("Open on Buy YES"));

    expect(stakeChip("50")).toHaveAttribute("aria-pressed", "false");
    expect(button("Buy YES")).toBeDisabled();
    expect(api.calls(TRADE)).toHaveLength(0);
  });
});

describe("turning the phone makes the sheet a dialog, which mounts the ticket afresh", () => {
  test("an unanswered trade is still there, and Try again still sends the same request", async () => {
    const { user, api } = await confirmBuy({ surface: "sheet", routes: { [TRADE]: noAnswer } });
    await screen.findByRole("alert");
    expect(screen.getByRole("dialog")).toHaveAttribute("data-slot", "drawer-content");

    act(() => setViewport("wide"));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveAttribute("data-slot", "dialog-content");
    expect(within(dialog).getByRole("alert")).toHaveTextContent("No connection. Try again.");
    expect(stakeChip("25")).toHaveAttribute("aria-pressed", "true");
    expect(stakeChip("25")).toBeDisabled();
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(api.calls(TRADE)).toHaveLength(1);

    api.on(TRADE, json(201, tradeResponse()));
    await user.click(button("Try again"));
    await screen.findByRole("heading", { name: /^Bought/ });
    expect(wire(api)[1]).toEqual(wire(api)[0]);
  });

  test("a receipt is still there", async () => {
    await confirmBuy({ surface: "sheet", routes: { [TRADE]: json(201, tradeResponse()) } });
    await screen.findByRole("heading", { name: /^Bought/ });

    act(() => setViewport("wide"));
    await new Promise((resolve) => setTimeout(resolve, 30));

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("data-slot", "dialog-content");
    expect(
      within(dialog).getByRole("heading", { name: "Bought 45.123456 YES shares" }),
    ).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Done" })).toBeInTheDocument();
  });

  test("the chosen chip and its quote are still there", async () => {
    const { user } = openTicket({ surface: "sheet", routes: { [QUOTE]: json(200, quote()) } });
    await user.click(stakeChip("50"));
    await waitFor(() => expect(button("Buy YES")).toBeEnabled());

    act(() => setViewport("wide"));
    await new Promise((resolve) => setTimeout(resolve, 30));

    expect(screen.getByRole("dialog")).toHaveAttribute("data-slot", "dialog-content");
    expect(stakeChip("50")).toHaveAttribute("aria-pressed", "true");
    await waitFor(() => expect(button("Buy YES")).toBeEnabled());
    expect(row("Shares")).toHaveTextContent("45.12");
  });
});

describe("QUOTE_STALE", () => {
  const moved = quote({
    shares: "44.871",
    average_price: 0.557,
    p_yes_after: 0.561,
    quote_version: 8,
  });
  const stale = apiError(
    409,
    "QUOTE_STALE",
    "The price moved. Review the new quote and confirm again.",
    { quote: moved },
  );

  test("shows the quote of the answer with what changed flashing, and a notice above the button", async () => {
    const { api } = await confirmBuy({ routes: { [TRADE]: stale } });

    const notice = await screen.findByRole("alert");
    expect(notice).toHaveTextContent("The price moved. Review the new quote and confirm again.");
    // A notice, not a fault: it is not in the red of an error.
    expect(notice).toHaveClass("text-foreground");
    expect(notice).not.toHaveClass("text-no-text");

    expect(row("Shares")).toHaveTextContent("44.87");
    expect(row("Average price")).toHaveTextContent("0.557");
    expect(row("New probability")).toHaveTextContent("56.1% YES");
    const flashing = (label: string) =>
      row(label).querySelector("[data-slot=flash]")?.className.includes("animate-price-flash");
    expect(flashing("Shares")).toBe(true);
    expect(flashing("Average price")).toBe(true);
    expect(flashing("New probability")).toBe(true);
    // What you pay did not change, so it does not flash.
    expect(row("You pay")).toHaveTextContent("25 tokens");
    expect(flashing("You pay")).toBe(false);

    // The quote came with the answer: nothing was asked for, and nothing was sent.
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(api.calls(QUOTE)).toHaveLength(1);
    expect(api.calls(TRADE)).toHaveLength(1);
  });

  test("brings the quote to review into view, for a screen too short to show it above the button", async () => {
    const intoView = vi.spyOn(Element.prototype, "scrollIntoView");
    await confirmBuy({ routes: { [TRADE]: stale } });
    await screen.findByRole("alert");

    const panel = document.querySelector("[data-slot=quote-panel]");
    expect(panel).toHaveTextContent("44.87");
    await waitFor(() => expect(intoView.mock.contexts).toContain(panel?.parentElement));
    // Only as far as it takes: a sheet that shows the panel already does not move.
    expect(intoView).toHaveBeenLastCalledWith({ block: "nearest" });
  });

  test("gives the button back, and the next Confirm is another request with another key", async () => {
    const { user, api } = await confirmBuy({ routes: { [TRADE]: stale } });
    await screen.findByRole("alert");

    const confirm = button("Buy YES");
    expect(confirm).toBeEnabled();
    expect(confirm).not.toHaveAttribute("aria-busy");
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
    expect(stakeChip("50")).toBeEnabled();

    api.on(TRADE, json(201, tradeResponse({ shares: "44.9" })));
    await user.click(confirm);
    await screen.findByRole("heading", { name: "Bought 44.9 YES shares" });

    const [first, second] = wire(api);
    expect(wire(api)).toHaveLength(2);
    expect(first?.body).toBe(BUY_25);
    // Guarded by the quote that was reviewed, not by the one that went stale.
    expect(second?.body).toBe(
      '{"side":"BUY","outcome":"YES","amount_cr":"25","min_shares":"44.871","quote_version":8}',
    );
    expect(second?.key).toMatch(UUID);
    expect(second?.key).not.toBe(first?.key);
  });

  test("on a replay ends the wait: the stored request is dropped like after any refusal", async () => {
    const { user, api } = await confirmBuy({ routes: { [TRADE]: noAnswer } });
    await screen.findByRole("alert");

    api.on(TRADE, stale);
    await user.click(button("Try again"));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        "The price moved. Review the new quote and confirm again.",
      ),
    );
    expect(row("Shares")).toHaveTextContent("44.87");
    api.on(TRADE, deferred().handler);
    await user.click(button("Buy YES"));

    await waitFor(() => expect(api.calls(TRADE)).toHaveLength(3));
    const [first, replay, next] = wire(api);
    expect(replay).toEqual(first);
    expect(next?.key).not.toBe(first?.key);
    expect(next?.body).toContain('"min_shares":"44.871"');
  });
});

describe("a refusal", () => {
  test("is shown above the button with the API's message, keeps the input, and the next Confirm has another key", async () => {
    const intoView = vi.spyOn(Element.prototype, "scrollIntoView");
    const { user, api } = await confirmBuy({
      routes: { [TRADE]: apiError(409, "INSUFFICIENT_BALANCE", "Not enough tokens for this.") },
    });

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Not enough tokens for this.");
    expect(alert).toHaveClass("text-no-text");
    // The quote on screen is the one that was confirmed: there is nothing to bring into view.
    expect(intoView).not.toHaveBeenCalled();
    expect(alert.compareDocumentPosition(button("Buy YES")) & 4).toBeTruthy();
    expect(stakeChip("25")).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();

    await waitFor(() => expect(button("Buy YES")).toBeEnabled());
    await user.click(button("Buy YES"));

    await waitFor(() => expect(api.calls(TRADE)).toHaveLength(2));
    const [first, second] = wire(api);
    expect(second?.key).toMatch(UUID);
    expect(second?.key).not.toBe(first?.key);
  });

  test("goes when the input changes: it was about what was sent", async () => {
    const { user } = await confirmBuy({
      routes: {
        [TRADE]: apiError(422, "TRADE_TOO_SMALL", "This trade is below the minimum size."),
      },
    });
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This trade is below the minimum size.",
    );

    await user.click(stakeChip("50"));

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    // And it does not come back with the chip.
    await user.click(stakeChip("25"));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  test("LIMIT_EXCEEDED refreshes the stake-left line from the answer's details", async () => {
    await confirmBuy({
      market: marketDetail({ viewer: marketViewer({ stake_left_cr: "100" }) }),
      routes: {
        [TRADE]: apiError(409, "LIMIT_EXCEEDED", "This would exceed a limit.", {
          limit: "MAX_STAKE_PER_USER_MARKET",
          stake_limit_cr: "80",
          stake_left_cr: "12.5",
        }),
      },
    });

    expect(await screen.findByRole("alert")).toHaveTextContent("This would exceed a limit.");
    expect(screen.getByText("12.5 of 80 tokens left in this market")).toBeInTheDocument();
    expect(screen.queryByText("100 of 100 tokens left in this market")).not.toBeInTheDocument();
  });

  test("LIMIT_EXCEEDED keeps its message when the market that is read again disallows the chip", async () => {
    const { user, api } = openTicket({ routes: { [QUOTE]: json(200, quote()) } });
    await user.click(stakeChip("25"));
    await waitFor(() => expect(button("Buy YES")).toBeEnabled());

    // The market as it is read again after the refusal: the limit leaves room for no chip.
    api.on(
      MARKET,
      json(
        200,
        marketDetail({
          viewer: marketViewer({
            stake_left_cr: "12.5",
            stake_presets: [
              { amount_cr: "25", allowed: false },
              { amount_cr: "50", allowed: false },
              { amount_cr: "100", allowed: false },
            ],
          }),
        }),
      ),
    );
    api.on(
      TRADE,
      apiError(409, "LIMIT_EXCEEDED", "This would exceed a limit.", {
        limit: "MAX_STAKE_PER_USER_MARKET",
        stake_limit_cr: "100",
        stake_left_cr: "12.5",
      }),
    );
    await user.click(button("Buy YES"));

    await waitFor(() => expect(stakeChip("25")).toBeDisabled());
    expect(screen.getByRole("alert")).toHaveTextContent("This would exceed a limit.");
    expect(screen.getByText("12.5 of 100 tokens left in this market")).toBeInTheDocument();
    expect(button("Buy YES")).toBeDisabled();
  });

  test("INSUFFICIENT_SHARES reads the market again, so the sell presets are current", async () => {
    const before = marketDetail({ viewer: marketViewerWithPosition() });
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
      market: before,
      start: SELL_YES,
      routes: {
        [QUOTE]: json(200, quote({ side: "SELL", shares: "91.645", amount_cr: "47.749024" })),
        [TRADE]: apiError(409, "INSUFFICIENT_SHARES", "You cannot sell more shares than you hold."),
      },
    });
    await user.click(sellChip("All"));
    await waitFor(() => expect(button("Sell YES shares")).toBeEnabled());
    const reads = api.calls(MARKET).length;

    api.on(MARKET, json(200, after));
    await user.click(button("Sell YES shares"));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "You cannot sell more shares than you hold.",
    );
    await waitFor(() => expect(api.calls(MARKET)).toHaveLength(reads + 1));
    await waitFor(() => expect(sellChip("All")).toHaveTextContent("40 shares"));
    expect(sellChip("Half")).toHaveTextContent("20 shares");
    // The message stays readable while the presets change under it.
    expect(screen.getByRole("alert")).toHaveTextContent(
      "You cannot sell more shares than you hold.",
    );
  });

  test("MARKET_NOT_OPEN reads the market again, so the ticket shows that trading has stopped", async () => {
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
      }),
    });
    const { user, api } = openTicket({
      routes: {
        [QUOTE]: json(200, quote()),
        [TRADE]: apiError(409, "MARKET_NOT_OPEN", "This market is not open for trading."),
      },
    });
    await user.click(stakeChip("25"));
    await waitFor(() => expect(button("Buy YES")).toBeEnabled());
    const reads = api.calls(MARKET).length;

    api.on(MARKET, json(200, closed));
    await user.click(button("Buy YES"));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This market is not open for trading.",
    );
    await waitFor(() => expect(api.calls(MARKET)).toHaveLength(reads + 1));
    await waitFor(() => expect(stakeChip("25")).toBeDisabled());
    expect(button("Buy YES")).toBeDisabled();
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  test("IDEMPOTENCY_KEY_REUSED shows one sentence of ours, and the next attempt has another key", async () => {
    const { user, api } = await confirmBuy({
      routes: {
        [TRADE]: apiError(
          422,
          "IDEMPOTENCY_KEY_REUSED",
          "This Idempotency-Key was already used for a different request.",
        ),
      },
    });

    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong. Try again.");
    expect(screen.queryByText(/Idempotency-Key/)).not.toBeInTheDocument();

    await user.click(button("Buy YES"));
    await waitFor(() => expect(api.calls(TRADE)).toHaveLength(2));
    expect(wire(api)[1]?.key).not.toBe(wire(api)[0]?.key);
  });

  test.each([
    [422, "TRADE_TOO_SMALL", "This trade is below the minimum size."],
    [422, "VALIDATION_ERROR", "The request is not valid."],
    [403, "FORBIDDEN", "You proposed this market, so you cannot trade it."],
    [404, "NOT_FOUND", "Not found."],
  ] as const)("a %i %s is shown as sent, in the sheet", async (status, code, message) => {
    await confirmBuy({ routes: { [TRADE]: apiError(status, code, message) } });

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(message);
    expect(ticketFrame()).toContainElement(alert);
    expect(ticketFrame()).toHaveAttribute("data-view", "form");
    expect(stakeChip("25")).toHaveAttribute("aria-pressed", "true");
  });
});
