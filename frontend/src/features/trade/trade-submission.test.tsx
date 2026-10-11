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
  stakeField,
  stakeSlider,
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

/** Opens the ticket on Buy YES, sets 25 with its preset, waits for the quote and taps Confirm. */
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
  test("sends the amount the field holds with the quote as its guard, under one Idempotency-Key", async () => {
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
    expect(stakeField()).toBeDisabled();
    expect(document.querySelector("[data-slot=slider]")).toHaveAttribute("data-disabled");
    // What is being sent stays on screen: the amount, and the quote it was confirmed with.
    expect(stakeField()).toHaveValue("25");
    expect(stakeChip("25")).toHaveAttribute("aria-pressed", "true");
    expect(stakeSlider()).toHaveAttribute("aria-valuenow", "25");
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
    expect(stakeField()).toBeDisabled();
    expect(stakeField()).toHaveValue("25");
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

  test("keeps the presets and the range it was made from, also when the market offers others by now", async () => {
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
            buy_slider: { min: 1, max: 20, step: 1 },
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
    // The slider is the one the trade was made on, standing on what was sent.
    expect(stakeSlider()).toHaveAttribute("aria-valuemax", "100");
    expect(stakeSlider()).toHaveAttribute("aria-valuenow", "25");
    expect(stakeField()).toHaveValue("25");
    expect(stakeField()).toBeDisabled();
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
              {
                outcome: "YES",
                portion: "HALF",
                shares: "22.91125",
                proceeds_cr: "12.294647",
                allowed: true,
              },
              {
                outcome: "YES",
                portion: "ALL",
                shares: "45.8225",
                proceeds_cr: "24.850327",
                allowed: true,
              },
              { outcome: "NO", portion: "HALF", shares: "0", proceeds_cr: "0", allowed: false },
              { outcome: "NO", portion: "ALL", shares: "0", proceeds_cr: "0", allowed: false },
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

  test("the amount that was entered and its quote are still there", async () => {
    const { user } = openTicket({ surface: "sheet", routes: { [QUOTE]: json(200, quote()) } });
    await user.type(stakeField(), "37.5");
    await waitFor(() => expect(button("Buy YES")).toBeEnabled());

    act(() => setViewport("wide"));
    await new Promise((resolve) => setTimeout(resolve, 30));

    expect(screen.getByRole("dialog")).toHaveAttribute("data-slot", "dialog-content");
    expect(stakeField()).toHaveValue("37.5");
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

  test("LIMIT_EXCEEDED keeps its message when the market that is read again disallows the preset, and Confirm waits for another amount", async () => {
    const limit = {
      limit: "MAX_STAKE_PER_USER_MARKET",
      stake_limit_cr: "100",
      stake_left_cr: "12.5",
    };
    const { user, api } = openTicket({ routes: { [QUOTE]: json(200, quote()) } });
    await user.click(stakeChip("25"));
    await waitFor(() => expect(button("Buy YES")).toBeEnabled());

    // The market as it is read again after the refusal: the limit leaves room for no
    // preset. The quote is about the same viewer, so it says what the trade said.
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
            buy_slider: { min: 1, max: 12, step: 1 },
          }),
        }),
      ),
    );
    api.on(QUOTE, apiError(409, "LIMIT_EXCEEDED", "This would exceed a limit.", limit));
    api.on(TRADE, apiError(409, "LIMIT_EXCEEDED", "This would exceed a limit.", limit));
    await user.click(button("Buy YES"));

    await waitFor(() => expect(stakeChip("25")).toBeDisabled());
    // The quote for this amount was asked for again: the one on screen did not hold.
    await waitFor(() => expect(api.calls(QUOTE)).toHaveLength(2));
    await waitFor(() => expect(button("Buy YES")).toBeDisabled());
    // Said once, where the trade's refusal stands: above the button.
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(screen.getByRole("alert")).toHaveTextContent("This would exceed a limit.");
    expect(ticketFrame()!.querySelector("[data-slot=sheet-footer]")).toContainElement(
      screen.getByRole("alert"),
    );
    expect(screen.getByText("12.5 of 100 tokens left in this market")).toBeInTheDocument();
    // The amount is the user's: it stays, and no trade goes out for it again.
    expect(stakeField()).toHaveValue("25");
    await user.click(button("Buy YES"));
    expect(api.calls(TRADE)).toHaveLength(1);

    // Another amount is judged afresh.
    api.on(QUOTE, json(200, quote({ amount_cr: "12" })));
    await user.clear(stakeField());
    await user.type(stakeField(), "12");
    await waitFor(() => expect(button("Buy YES")).toBeEnabled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  test.each([
    [409, "INSUFFICIENT_BALANCE", "Not enough tokens for this."],
    [409, "LIMIT_EXCEEDED", "This would exceed a limit."],
  ] as const)(
    "after a %i %s the quote on screen is asked for again, and Confirm waits for it",
    async (status, code, message) => {
      const verdict = deferred();
      const { user, api } = await confirmBuy({ routes: { [TRADE]: verdict.handler } });
      const again = deferred();
      api.on(QUOTE, again.handler);
      verdict.resolve(apiError(status, code, message));

      expect(await screen.findByRole("alert")).toHaveTextContent(message);
      await waitFor(() => expect(api.calls(QUOTE)).toHaveLength(2));
      expect([...api.lastCall(QUOTE)!.query.entries()]).toEqual([
        ["side", "BUY"],
        ["outcome", "YES"],
        ["amount_cr", "25"],
      ]);
      // On its way: the figures that guarded the refused trade count for nothing.
      await waitFor(() => expect(button("Buy YES")).toBeDisabled());
      expect(row("Shares").querySelector("[data-slot=skeleton]")).toBeInTheDocument();
      await user.click(button("Buy YES"));
      expect(api.calls(TRADE)).toHaveLength(1);

      again.resolve(json(200, quote({ shares: "44.9", quote_version: 9 })));

      await waitFor(() => expect(button("Buy YES")).toBeEnabled());
      expect(row("Shares")).toHaveTextContent("44.9");
      // The refusal is still to read.
      expect(screen.getByRole("alert")).toHaveTextContent(message);
    },
  );

  test("a refusal that is not about what the ticket showed leaves the quote as it is", async () => {
    const { api } = await confirmBuy({
      routes: {
        [TRADE]: apiError(422, "TRADE_TOO_SMALL", "This trade is below the minimum size."),
      },
    });

    await screen.findByRole("alert");
    await new Promise((resolve) => setTimeout(resolve, 30));

    expect(api.calls(QUOTE)).toHaveLength(1);
    expect(button("Buy YES")).toBeEnabled();
  });

  test("INSUFFICIENT_SHARES reads the market again, so the sell presets are current", async () => {
    const before = marketDetail({ viewer: marketViewerWithPosition() });
    const after = marketDetail({
      viewer: marketViewerWithPosition({
        sell_presets: [
          { outcome: "YES", portion: "HALF", shares: "20", proceeds_cr: "10.73241", allowed: true },
          { outcome: "YES", portion: "ALL", shares: "40", proceeds_cr: "21.464821", allowed: true },
          { outcome: "NO", portion: "HALF", shares: "0", proceeds_cr: "0", allowed: false },
          { outcome: "NO", portion: "ALL", shares: "0", proceeds_cr: "0", allowed: false },
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

describe("a typed amount", () => {
  const BUY_TYPED =
    '{"side":"BUY","outcome":"YES","amount_cr":"37.50","min_shares":"67.4","quote_version":7}';
  const forTyped = quote({ amount_cr: "37.5", shares: "67.4" });

  /** Opens the ticket on Buy YES, types 37.50, waits for the quote and taps Confirm. */
  async function confirmTyped(options: TicketOptions = {}) {
    const ticket = openTicket({
      ...options,
      routes: { [QUOTE]: json(200, forTyped), ...options.routes },
    });
    await ticket.user.type(stakeField(), "37.50");
    await waitFor(() => expect(button("Buy YES")).toBeEnabled());
    await ticket.user.click(button("Buy YES"));
    return ticket;
  }

  test("is sent as it was typed, with the quote as its guard, under one Idempotency-Key", async () => {
    const { api } = await confirmTyped({ routes: { [TRADE]: deferred().handler } });

    await waitFor(() => expect(api.calls(TRADE)).toHaveLength(1));
    // The zero that was typed is in the body: the text is not rewritten on its way.
    expect(sent(api.lastCall(TRADE))).toEqual({
      key: expect.stringMatching(UUID),
      body: BUY_TYPED,
    });
  });

  test("while the trade is on its way the field shows what was sent and takes nothing", async () => {
    const answer = deferred();
    const { user } = await confirmTyped({ routes: { [TRADE]: answer.handler } });

    expect(stakeField()).toBeDisabled();
    expect(stakeField()).toHaveValue("37.50");
    expect(document.querySelector("[data-slot=slider]")).toHaveAttribute("data-disabled");
    await user.type(stakeField(), "9");
    expect(stakeField()).toHaveValue("37.50");

    answer.resolve(json(201, tradeResponse()));
    await screen.findByRole("heading", { name: /^Bought/ });
  });

  test("without a verdict, Try again sends the stored body with the stored key: the field cannot have changed it", async () => {
    const { user, api } = await confirmTyped({ routes: { [TRADE]: noAnswer } });
    await screen.findByRole("alert");

    expect(stakeField()).toBeDisabled();
    expect(stakeField()).toHaveValue("37.50");
    // A later quote must not find its way into the request.
    api.on(QUOTE, json(200, quote({ amount_cr: "37.5", shares: "60", quote_version: 11 })));
    api.on(TRADE, json(201, tradeResponse()));
    await user.click(button("Try again"));
    await screen.findByRole("heading", { name: /^Bought/ });

    const [first, second] = wire(api);
    expect(wire(api)).toHaveLength(2);
    expect(first).toEqual({ key: expect.stringMatching(UUID), body: BUY_TYPED });
    expect(second).toEqual(first);
  });

  test("an unanswered trade is there again after the ticket left the screen, with the amount that was sent", async () => {
    const { user, api } = await confirmTyped({ routes: { [TRADE]: errorPage(504) } });
    await screen.findByRole("alert");

    await user.click(button("Close the sheet"));
    await new Promise((resolve) => setTimeout(resolve, 30));
    await user.click(button("Open on Buy NO"));

    expect(ticketFrame()).toHaveAttribute("data-outcome", "YES");
    expect(stakeField()).toHaveValue("37.50");
    expect(stakeField()).toBeDisabled();
    expect(row("Shares")).toHaveTextContent("67.4");
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(api.calls(TRADE)).toHaveLength(1);

    api.on(TRADE, deferred().handler);
    await user.click(button("Try again"));
    await waitFor(() => expect(api.calls(TRADE)).toHaveLength(2));
    expect(wire(api)[1]).toEqual(wire(api)[0]);
  });

  test("after a refusal the amount can be changed, and the next Confirm is another body with another key", async () => {
    const { user, api } = await confirmTyped({
      routes: { [TRADE]: apiError(409, "LIMIT_EXCEEDED", "This would exceed a limit.") },
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("This would exceed a limit.");
    expect(stakeField()).toBeEnabled();
    expect(stakeField()).toHaveValue("37.50");

    api.on(QUOTE, json(200, quote({ amount_cr: "30", shares: "54" })));
    await user.clear(stakeField());
    await user.type(stakeField(), "30");
    // The refusal was about what was sent.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await waitFor(() => expect(button("Buy YES")).toBeEnabled());
    api.on(TRADE, deferred().handler);
    await user.click(button("Buy YES"));

    await waitFor(() => expect(api.calls(TRADE)).toHaveLength(2));
    const [first, second] = wire(api);
    expect(first?.body).toBe(BUY_TYPED);
    expect(second?.body).toBe(
      '{"side":"BUY","outcome":"YES","amount_cr":"30","min_shares":"54","quote_version":7}',
    );
    expect(second?.key).toMatch(UUID);
    expect(second?.key).not.toBe(first?.key);
  });

  test("QUOTE_STALE keeps the amount: the next Confirm sends it again, guarded by the quote that was reviewed", async () => {
    const moved = quote({ amount_cr: "37.5", shares: "66.9", quote_version: 8 });
    const { user, api } = await confirmTyped({
      routes: {
        [TRADE]: apiError(
          409,
          "QUOTE_STALE",
          "The price moved. Review the new quote and confirm again.",
          {
            quote: moved,
          },
        ),
      },
    });
    const asked = api.calls(QUOTE).length;
    await screen.findByRole("alert");

    expect(stakeField()).toHaveValue("37.50");
    expect(row("Shares")).toHaveTextContent("66.9");
    // The quote came with the answer: nothing was asked for.
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(api.calls(QUOTE)).toHaveLength(asked);
    api.on(TRADE, deferred().handler);
    await user.click(button("Buy YES"));

    await waitFor(() => expect(api.calls(TRADE)).toHaveLength(2));
    const [first, second] = wire(api);
    expect(second?.body).toBe(
      '{"side":"BUY","outcome":"YES","amount_cr":"37.50","min_shares":"66.9","quote_version":8}',
    );
    expect(second?.key).not.toBe(first?.key);
  });

  test("tapped twice before the screen has drawn the spinner is still one request", async () => {
    const { user, api } = openTicket({
      routes: { [QUOTE]: json(200, forTyped), [TRADE]: deferred().handler },
    });
    await user.type(stakeField(), "37.50");
    await waitFor(() => expect(button("Buy YES")).toBeEnabled());
    const confirm = button("Buy YES");

    act(() => {
      confirm.click();
      confirm.click();
    });

    await waitFor(() => expect(api.calls(TRADE)).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(api.calls(TRADE)).toHaveLength(1);
  });

  test("an amount that changed after its quote cannot be confirmed with that quote", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      // No route for a trade: a request that went out with a guard for another amount
      // fails the test.
      const { user, api } = openTicket({
        routes: { [QUOTE]: json(200, forTyped) },
        tunables: { quote_debounce_ms: 60_000 },
      });
      await user.type(stakeField(), "37.5");
      await act(() => vi.advanceTimersByTimeAsync(61_000));
      await waitFor(() => expect(button("Buy YES")).toBeEnabled());

      // One more digit. What is on screen was quoted for 37.5, not for 37.55.
      await user.type(stakeField(), "5");

      expect(stakeField()).toHaveValue("37.55");
      expect(button("Buy YES")).toBeDisabled();
      expect(row("Shares")).not.toHaveTextContent("67.4");
      await user.click(button("Buy YES"));
      expect(api.calls(TRADE)).toHaveLength(0);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("the sheet while its trade is on its way", () => {
  const cross = () => screen.getByRole("button", { name: "Close" });

  test("cannot be closed: the cross stays, disabled, and Escape does nothing", async () => {
    const answer = deferred();
    const { user, api } = await confirmBuy({
      surface: "sheet",
      routes: { [TRADE]: answer.handler },
    });
    await waitFor(() => expect(api.calls(TRADE)).toHaveLength(1));

    expect(cross()).toBeDisabled();
    await user.keyboard("{Escape}");
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(button("Buy YES")).toHaveAttribute("aria-busy", "true");

    // The verdict is seen where the trade was made.
    answer.resolve(json(201, tradeResponse()));
    expect(await screen.findByRole("heading", { name: /^Bought/ })).toBeInTheDocument();
  });

  test("the receipt keeps the cross, which closes it as Done does", async () => {
    const { user, changes } = await confirmBuy({
      surface: "sheet",
      routes: { [TRADE]: json(201, tradeResponse()) },
    });
    await screen.findByRole("heading", { name: /^Bought/ });

    await waitFor(() => expect(cross()).toBeEnabled());
    await user.click(cross());

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    // The next opening is an empty form.
    await new Promise((resolve) => setTimeout(resolve, 30));
    await user.click(button("Open on Buy YES"));
    await waitFor(() => expect(ticketFrame()).toHaveAttribute("data-view", "form"));
    expect(stakeField()).toHaveValue("");
    expect(changes).toEqual([]);
  });

  test("can be closed again once the trade was refused", async () => {
    const { user } = await confirmBuy({
      surface: "sheet",
      routes: { [TRADE]: apiError(409, "INSUFFICIENT_BALANCE", "Not enough tokens for this.") },
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("Not enough tokens for this.");

    await waitFor(() => expect(cross()).toBeEnabled());
    await user.keyboard("{Escape}");

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  test("a trade without an answer can be put away, and is locked in again while Try again is on its way", async () => {
    const { user, api } = await confirmBuy({ surface: "sheet", routes: { [TRADE]: noAnswer } });
    await screen.findByRole("alert");

    // Nothing is on its way while it waits for a tap.
    await waitFor(() => expect(cross()).toBeEnabled());

    const replay = deferred();
    api.on(TRADE, replay.handler);
    await user.click(button("Try again"));
    await waitFor(() => expect(api.calls(TRADE)).toHaveLength(2));

    expect(cross()).toBeDisabled();
    await user.keyboard("{Escape}");
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    replay.resolve(json(201, tradeResponse()));
    await screen.findByRole("heading", { name: /^Bought/ });
    await waitFor(() => expect(cross()).toBeEnabled());
  });

  test("the same holds for the dialog from 768 px", async () => {
    setViewport("wide");
    const answer = deferred();
    const { user, api } = await confirmBuy({
      surface: "sheet",
      routes: { [TRADE]: answer.handler },
    });
    await waitFor(() => expect(api.calls(TRADE)).toHaveLength(1));

    expect(screen.getByRole("dialog")).toHaveAttribute("data-slot", "dialog-content");
    expect(cross()).toBeDisabled();
    await user.keyboard("{Escape}");
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    answer.resolve(apiError(409, "INSUFFICIENT_BALANCE", "Not enough tokens for this."));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not enough tokens for this.");
    await waitFor(() => expect(cross()).toBeEnabled());
  });

  test("a sheet with a form in it closes as ever", async () => {
    const { user } = openTicket({ surface: "sheet", routes: { [QUOTE]: json(200, quote()) } });
    await user.click(stakeChip("25"));
    await waitFor(() => expect(button("Buy YES")).toBeEnabled());

    expect(cross()).toBeEnabled();
    await user.keyboard("{Escape}");

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});
