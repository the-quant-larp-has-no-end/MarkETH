import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, test } from "vitest";

import {
  holdings,
  MARKET_ID,
  marketSummary,
  OTHER_MARKET_ID,
  portfolio,
  portfolioEntry,
  tradeItem,
} from "@/test/fixtures";
import { apiError, deferred, json, mockApi, noAnswer, type Route } from "@/test/http";
import { navigation } from "@/test/navigation";
import { intersect } from "@/test/observers";
import { renderWithProviders } from "@/test/render";

import { PortfolioScreen } from "./portfolio-screen";

const POSITIONS: Route = "GET /api/me/positions";
const TRADES: Route = "GET /api/me/trades";

const UNAVAILABLE = apiError(
  503,
  "SERVICE_UNAVAILABLE",
  "This is temporarily unavailable. Try again shortly.",
);

/** Two markets that are not settled, then two that are: the order the API sends. */
const MIXED = portfolio({
  total_value_cr: "62.5",
  items: [
    portfolioEntry({
      market: marketSummary({ id: "open-1", question: "Will the Mensa serve rösti on Friday?" }),
      holdings: holdings({ shares: "91.645", value_cr: "50.221461" }),
      value_cr: "50.221461",
    }),
    portfolioEntry({
      market: marketSummary({
        id: "open-2",
        question: "Will the ASVZ open on the holiday?",
        status: "HALTED",
        trading_open: false,
      }),
      holdings: holdings(),
      value_cr: "0",
    }),
    portfolioEntry({
      market: marketSummary({
        id: "settled-1",
        question: "Did the Polybahn run all week?",
        status: "RESOLVED",
        trading_open: false,
        outcome: "YES",
      }),
      holdings: holdings({ shares: "40", value_cr: "40" }),
      value_cr: "40",
      payout_cr: "40",
    }),
    portfolioEntry({
      market: marketSummary({
        id: "settled-2",
        question: "Was the CAB open at midnight?",
        status: "VOID",
        trading_open: false,
        outcome: "VOID",
      }),
      holdings: holdings({}, { shares: "12.5", value_cr: "0" }),
      value_cr: "0",
      refund_cr: "7.25",
    }),
  ],
});

const summary = () => document.querySelector("[data-slot=portfolio-summary]") as HTMLElement | null;
const skeletonRows = () => document.querySelectorAll("[data-slot=row-skeletons] > li");
const group = (name: string) => screen.getByRole("region", { name });
const questions = (within_: HTMLElement) =>
  within(within_)
    .getAllByRole("link")
    .map((link) => link.querySelector(".type-body")?.textContent);

async function openHistory(user: ReturnType<typeof renderWithProviders>["user"]) {
  await user.click(screen.getByRole("tab", { name: "History" }));
}

beforeEach(() => navigation.go("/portfolio"));

describe("the frame", () => {
  test("a main screen whose one h1 names it, with the two views as tabs", async () => {
    mockApi({ [POSITIONS]: json(200, MIXED) });
    renderWithProviders(<PortfolioScreen />);

    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading).toHaveTextContent("Portfolio");
    expect(heading).toHaveClass("sr-only");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);

    const tabs = within(screen.getByRole("tablist")).getAllByRole("tab");
    expect(tabs.map((tab) => tab.textContent)).toEqual(["Positions", "History"]);
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
    expect(tabs[1]).toHaveAttribute("aria-selected", "false");
    await screen.findByText("Value of open positions");
  });
});

describe("loading", () => {
  test("a skeleton summary card and five skeleton rows, then the content", async () => {
    const answer = deferred();
    mockApi({ [POSITIONS]: answer.handler });
    renderWithProviders(<PortfolioScreen />);

    expect(document.querySelector("[data-slot=portfolio-summary-skeleton]")).toBeInTheDocument();
    expect(skeletonRows()).toHaveLength(5);
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
    expect(summary()).not.toBeInTheDocument();

    answer.resolve(json(200, MIXED));

    expect(await screen.findByText("Value of open positions")).toBeInTheDocument();
    expect(skeletonRows()).toHaveLength(0);
    expect(document.querySelector("[data-slot=portfolio-summary-skeleton]")).toBeNull();
  });
});

describe("the summary card", () => {
  test("shows total_value_cr as the API sent it, in number-lg", async () => {
    mockApi({ [POSITIONS]: json(200, MIXED) });
    renderWithProviders(<PortfolioScreen />);

    await screen.findByText("Value of open positions");
    const amount = summary()!.querySelector("[data-slot=amount]")!;
    expect(amount).toHaveTextContent("62.5 tokens");
    expect(amount).toHaveClass("type-number-lg");
  });

  test("the figure is the API's total, never a sum of the rows", async () => {
    // Rows worth 50.221461 and 40 under a total the API says is 1: the card says 1.
    mockApi({ [POSITIONS]: json(200, { ...MIXED, total_value_cr: "1" }) });
    renderWithProviders(<PortfolioScreen />);

    await screen.findByText("Value of open positions");
    expect(within(summary()!).getByText("1 token")).toBeInTheDocument();
    expect(summary()).not.toHaveTextContent("90");
  });

  test("an amount is cut and grouped as a string: no number could hold this one", async () => {
    mockApi({
      [POSITIONS]: json(200, { ...MIXED, total_value_cr: "123456789012345678901234.999999" }),
    });
    renderWithProviders(<PortfolioScreen />);

    await screen.findByText("Value of open positions");
    // Cut after the second decimal, not rounded up; every digit of the whole part kept.
    expect(summary()).toHaveTextContent("123,456,789,012,345,678,901,234.99");
    expect(summary()).not.toHaveTextContent("235");
    expect(summary()).not.toHaveTextContent("e+");
  });
});

describe("Positions", () => {
  test("the rows stand under Open and Settled, each group in the API's order", async () => {
    mockApi({ [POSITIONS]: json(200, MIXED) });
    renderWithProviders(<PortfolioScreen />);

    await screen.findByRole("region", { name: "Open" });
    expect(questions(group("Open"))).toEqual([
      "Will the Mensa serve rösti on Friday?",
      "Will the ASVZ open on the holiday?",
    ]);
    expect(questions(group("Settled"))).toEqual([
      "Did the Polybahn run all week?",
      "Was the CAB open at midnight?",
    ]);
    expect(screen.getByRole("heading", { level: 2, name: "Open" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Settled" })).toBeInTheDocument();
  });

  test("a market is settled by its status alone: RESOLVED and VOID, nothing else", async () => {
    mockApi({
      [POSITIONS]: json(
        200,
        portfolio({
          items: [
            portfolioEntry({
              market: marketSummary({ id: "a", question: "Closed one", status: "CLOSED" }),
            }),
            portfolioEntry({
              market: marketSummary({ id: "b", question: "Paused one", status: "HALTED" }),
            }),
          ],
        }),
      ),
    });
    renderWithProviders(<PortfolioScreen />);

    await screen.findByRole("region", { name: "Open" });
    expect(questions(group("Open"))).toEqual(["Closed one", "Paused one"]);
    // A group without rows is left out, heading and all.
    expect(screen.queryByRole("heading", { name: "Settled" })).not.toBeInTheDocument();
  });

  test("an open row names its holdings and shows value_cr; one without shares says so", async () => {
    mockApi({ [POSITIONS]: json(200, MIXED) });
    renderWithProviders(<PortfolioScreen />);

    const [held, none] = within(await screen.findByRole("region", { name: "Open" })).getAllByRole(
      "link",
    );
    expect(held).toHaveTextContent("91.64 YES shares");
    expect(held).toHaveTextContent("50.22 tokens");
    expect(held).not.toHaveTextContent("Paid out");
    expect(none).toHaveTextContent("No shares held");
  });

  test("a settled row shows Paid out with payout_cr, or Refunded with refund_cr, in place of value_cr", async () => {
    mockApi({ [POSITIONS]: json(200, MIXED) });
    renderWithProviders(<PortfolioScreen />);

    const [paid, refunded] = within(
      await screen.findByRole("region", { name: "Settled" }),
    ).getAllByRole("link");
    expect(paid).toHaveTextContent("Paid out");
    expect(paid).toHaveTextContent("40 tokens");
    expect(paid).toHaveTextContent("Resolved YES");
    expect(refunded).toHaveTextContent("Refunded");
    expect(refunded).toHaveTextContent("7.25 tokens");
    expect(refunded).toHaveTextContent("Voided");
    expect(refunded).toHaveTextContent("12.5 NO shares");
  });

  test("a row opens its market", async () => {
    mockApi({ [POSITIONS]: json(200, portfolio()) });
    const { user } = renderWithProviders(<PortfolioScreen />);

    const row = within(await screen.findByRole("region", { name: "Open" })).getByRole("link");
    expect(row).toHaveAttribute("href", `/markets/${MARKET_ID}`);
    await user.click(row);

    expect(navigation.pathname).toBe(`/markets/${MARKET_ID}`);
  });

  test("empty: the campus line and a primary button to the markets", async () => {
    mockApi({ [POSITIONS]: json(200, portfolio({ total_value_cr: "0", items: [] })) });
    renderWithProviders(<PortfolioScreen />);

    expect(await screen.findByText("No positions yet. Your cup is empty.")).toBeInTheDocument();
    const browse = screen.getByRole("link", { name: "Browse markets" });
    expect(browse).toHaveAttribute("href", "/");
    expect(browse).toHaveAttribute("data-variant", "primary");
    expect(document.querySelectorAll("[data-slot=button][data-variant=primary]")).toHaveLength(1);
    expect(screen.queryByRole("heading", { name: "Open" })).not.toBeInTheDocument();
    // The card still says what there is: nothing.
    expect(summary()!.querySelector("[data-slot=amount]")).toHaveTextContent("0 tokens");
  });

  test("error: a banner with the API's message and Try again, which reads the positions again", async () => {
    const api = mockApi({ [POSITIONS]: UNAVAILABLE });
    const { user } = renderWithProviders(<PortfolioScreen />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This is temporarily unavailable. Try again shortly.",
    );
    // It is said once: no second banner, and no card with a figure nobody sent.
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(summary()).not.toBeInTheDocument();
    expect(skeletonRows()).toHaveLength(0);

    api.on(POSITIONS, json(200, MIXED));
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByRole("region", { name: "Open" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(summary()).toBeInTheDocument();
    expect(api.calls(POSITIONS)).toHaveLength(2);
  });

  test("no connection has its own sentence", async () => {
    mockApi({ [POSITIONS]: noAnswer });
    renderWithProviders(<PortfolioScreen />);

    expect(await screen.findByRole("alert")).toHaveTextContent("No connection. Try again.");
  });

  test("RATE_LIMITED disables Try again for the time the API names", async () => {
    mockApi({
      [POSITIONS]: apiError(429, "RATE_LIMITED", "Too many requests. Try again later.", {
        retry_after_seconds: 20,
      }),
    });
    renderWithProviders(<PortfolioScreen />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Too many requests. Try again later.",
    );
    expect(screen.getByRole("button", { name: /^Try again in \d+ s$/ })).toBeDisabled();
  });
});

describe("History", () => {
  const firstPage = json(200, {
    items: [
      tradeItem({
        id: "t-3",
        market_id: MARKET_ID,
        market_question: "Will the Polybahn run without interruption?",
        side: "SELL",
        outcome: "NO",
        amount_cr: "12.5",
        shares: "30.123456",
        created_at: "2026-10-04T10:00:00Z",
      }),
      tradeItem({ id: "t-2", market_question: "Second trade" }),
    ],
    next_cursor: "page-2",
  });
  const lastPage = json(200, {
    items: [tradeItem({ id: "t-1", market_id: OTHER_MARKET_ID, market_question: "Oldest trade" })],
    next_cursor: null,
  });

  test("the trades are asked for when the tab is opened, without a page size", async () => {
    const api = mockApi({ [POSITIONS]: json(200, MIXED), [TRADES]: lastPage });
    const { user } = renderWithProviders(<PortfolioScreen />);
    await screen.findByRole("region", { name: "Open" });
    expect(api.calls(TRADES)).toHaveLength(0);

    await openHistory(user);

    expect(await screen.findByText("Oldest trade")).toBeInTheDocument();
    expect(api.calls(TRADES)).toHaveLength(1);
    expect(api.lastCall(TRADES)?.query.toString()).toBe("");
    expect(screen.getByRole("tab", { name: "History" })).toHaveAttribute("aria-selected", "true");
    // The summary card belongs to the screen, not to a tab.
    expect(summary()).toBeInTheDocument();
  });

  test("a row says what was done and when, shows the amount and the shares, and opens its market", async () => {
    mockApi({ [POSITIONS]: json(200, MIXED), [TRADES]: firstPage });
    const { user } = renderWithProviders(<PortfolioScreen />);
    await openHistory(user);

    const row = (await screen.findAllByRole("link")).find((link) =>
      link.textContent?.includes("Will the Polybahn run"),
    )!;
    expect(row).toHaveAttribute("href", `/markets/${MARKET_ID}`);
    expect(row).toHaveTextContent("Sold NO");
    expect(row).toHaveTextContent("4 Oct 2026, 12:00");
    expect(row).toHaveTextContent("12.5 tokens");
    expect(row).toHaveTextContent("30.12 shares");
  });

  test("more trades load with next_cursor when the end of the list comes into view, in the API's order", async () => {
    const api = mockApi({
      [POSITIONS]: json(200, MIXED),
      [TRADES]: (request) => (request.query.has("cursor") ? lastPage : firstPage),
    });
    const { user } = renderWithProviders(<PortfolioScreen />);
    await openHistory(user);
    await screen.findByText("Second trade");
    expect(screen.queryByText("Oldest trade")).not.toBeInTheDocument();

    act(() => intersect());

    expect(await screen.findByText("Oldest trade")).toBeInTheDocument();
    expect(api.lastCall(TRADES)?.query.toString()).toBe("cursor=page-2");
    const shown = within(screen.getByRole("tabpanel"))
      .getAllByRole("link")
      .map((link) => link.querySelector(".type-body")?.textContent);
    expect(shown).toEqual([
      "Will the Polybahn run without interruption?",
      "Second trade",
      "Oldest trade",
    ]);

    // The last page is there: nothing more is asked for.
    await waitFor(() =>
      expect(document.querySelector("[data-slot=load-more]")).not.toBeInTheDocument(),
    );
    act(() => intersect());
    expect(api.calls(TRADES)).toHaveLength(2);
  });

  test("a page that failed keeps the rows that are there and asks again only on tap", async () => {
    const api = mockApi({
      [POSITIONS]: json(200, MIXED),
      [TRADES]: (request) => (request.query.has("cursor") ? UNAVAILABLE : firstPage),
    });
    const { user } = renderWithProviders(<PortfolioScreen />);
    await openHistory(user);
    await screen.findByText("Second trade");

    act(() => intersect());

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This is temporarily unavailable. Try again shortly.",
    );
    expect(screen.getByText("Second trade")).toBeInTheDocument();
    const asked = api.calls(TRADES).length;

    api.on(TRADES, lastPage);
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByText("Oldest trade")).toBeInTheDocument();
    expect(api.calls(TRADES)).toHaveLength(asked + 1);
  });

  test("loading: five skeleton rows", async () => {
    const answer = deferred();
    mockApi({ [POSITIONS]: json(200, MIXED), [TRADES]: answer.handler });
    const { user } = renderWithProviders(<PortfolioScreen />);
    await screen.findByRole("region", { name: "Open" });

    await openHistory(user);

    expect(skeletonRows()).toHaveLength(5);
    expect(screen.getByRole("status")).toHaveTextContent("Loading");

    answer.resolve(lastPage);
    expect(await screen.findByText("Oldest trade")).toBeInTheDocument();
    expect(skeletonRows()).toHaveLength(0);
  });

  test("empty: No trades yet.", async () => {
    mockApi({
      [POSITIONS]: json(200, portfolio({ total_value_cr: "0", items: [] })),
      [TRADES]: json(200, { items: [], next_cursor: null }),
    });
    const { user } = renderWithProviders(<PortfolioScreen />);
    await screen.findByText("No positions yet. Your cup is empty.");

    await openHistory(user);

    expect(await screen.findByText("No trades yet.")).toBeInTheDocument();
    // One campus line per screen at most: the cup belongs to the other tab.
    expect(screen.queryByText("No positions yet. Your cup is empty.")).not.toBeInTheDocument();
  });

  test("error: a banner with Try again, and the positions are left alone", async () => {
    const api = mockApi({ [POSITIONS]: json(200, MIXED), [TRADES]: UNAVAILABLE });
    const { user } = renderWithProviders(<PortfolioScreen />);
    await openHistory(user);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This is temporarily unavailable. Try again shortly.",
    );

    api.on(TRADES, lastPage);
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByText("Oldest trade")).toBeInTheDocument();
    expect(api.calls(POSITIONS)).toHaveLength(1);
  });
});
