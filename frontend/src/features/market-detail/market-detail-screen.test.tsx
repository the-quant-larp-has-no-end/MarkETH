import { act, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { keys } from "@/lib/api/keys";
import { sizeCharts } from "@/test/charts";
import { installFakeEventSource } from "@/test/fake-event-source";
import {
  adminMarketDetail,
  history,
  holdings,
  MARKET_ID,
  marketDetail,
  marketSummary,
  marketViewer,
  marketViewerWithPosition,
  TODAY,
} from "@/test/fixtures";
import { apiError, deferred, errorPage, json, mockApi, noAnswer } from "@/test/http";
import { followHistoryWrites, navigation } from "@/test/navigation";
import { createTestQueryClient, renderWithProviders } from "@/test/render";

import { MarketDetailScreen } from "./index";
import { HISTORY, MARKET, mockMarketApi, PATH } from "./testing";

vi.mock("@/features/trade", async () => (await import("./testing")).tradeFeature);

const QUESTION = "Will the Polybahn run without interruption during the exam session?";

const open = () => renderWithProviders(<MarketDetailScreen marketId={MARKET_ID} />);

/** The market has been read: the skeleton, which is a status too, is gone. */
const loaded = () => screen.findByRole("heading", { level: 1, name: QUESTION });

const section = (name: string) => screen.getByRole("region", { name });
const querySection = (name: string) => screen.queryByRole("region", { name });
const button = (name: string | RegExp) => screen.getByRole("button", { name });

/** A market in a settled or closed status, as the API sends one. */
const closed = (overrides: Parameters<typeof marketDetail>[0] = {}) =>
  marketDetail({
    status: "CLOSED",
    trading_open: false,
    viewer: marketViewer({ can_trade: false, cannot_trade_reason: "MARKET_NOT_OPEN" }),
    ...overrides,
  });

beforeEach(() => {
  navigation.go(PATH);
  followHistoryWrites();
  sizeCharts();
  // The deadline is said by the device's clock, and the screen sets a timer for the
  // market's close time: the tests stand on one day.
  vi.useFakeTimers({ now: TODAY, toFake: ["Date"] });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("MarketDetailScreen", () => {
  describe("loading", () => {
    test("shows skeletons in the shape of the screen until the market has answered", async () => {
      const answer = deferred();
      mockApi({ [MARKET]: answer.handler, [HISTORY]: json(200, history()) });

      open();

      expect(screen.getByRole("status")).toHaveTextContent("Loading market");
      expect(document.querySelector("[data-slot=market-detail-skeleton]")).toBeInTheDocument();
      expect(document.querySelector("[data-slot=chart-skeleton]")).toBeInTheDocument();
      // The screen has its one h1 before the question is known.
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Market");
      expect(screen.getByRole("heading", { level: 1 })).toHaveClass("sr-only");

      await act(async () => answer.resolve(json(200, marketDetail())));

      expect(await screen.findByRole("heading", { level: 1, name: QUESTION })).toBeInTheDocument();
      expect(document.querySelector("[data-slot=market-detail-skeleton]")).not.toBeInTheDocument();
    });

    test("has a back arrow and no title in its top bar", () => {
      mockApi({ [MARKET]: deferred().handler });

      open();

      const bar = document.querySelector("[data-slot=top-bar]")!;
      expect(within(bar as HTMLElement).getByRole("button", { name: "Back" })).toBeInTheDocument();
      expect(within(bar as HTMLElement).queryByRole("heading")).not.toBeInTheDocument();
    });

    test("the chart has its own skeleton while the history is on its way", async () => {
      const points = deferred();
      mockApi({ [MARKET]: json(200, marketDetail()), [HISTORY]: points.handler });

      open();

      await screen.findByRole("heading", { name: QUESTION });
      expect(document.querySelector("[data-slot=chart-skeleton]")).toBeInTheDocument();
      expect(screen.queryByRole("application")).not.toBeInTheDocument();

      await act(async () => points.resolve(json(200, history())));

      expect(await screen.findByRole("application")).toBeInTheDocument();
      expect(document.querySelector("[data-slot=chart-skeleton]")).not.toBeInTheDocument();
    });

    test("asks for the history only once it knows the market is no proposal", async () => {
      const answer = deferred();
      const api = mockApi({ [MARKET]: answer.handler, [HISTORY]: json(200, history()) });

      open();
      await waitFor(() => expect(api.calls(MARKET)).toHaveLength(1));
      expect(api.calls(HISTORY)).toHaveLength(0);

      await act(async () => answer.resolve(json(200, marketDetail())));

      await waitFor(() => expect(api.calls(HISTORY)).toHaveLength(1));
    });

    test("asks for the history at once for a market it has seen in a list", async () => {
      const api = mockApi({ [MARKET]: deferred().handler, [HISTORY]: json(200, history()) });
      const client = createTestQueryClient();
      client.setQueryData(keys.markets.list(["OPEN", "HALTED"]), { items: [marketSummary()] });

      renderWithProviders(<MarketDetailScreen marketId={MARKET_ID} />, { client });

      await waitFor(() => expect(api.calls(HISTORY)).toHaveLength(1));
      expect(screen.getByRole("status")).toHaveTextContent("Loading market");
    });
  });

  describe("a market the API does not know", () => {
    test("is the not found screen", async () => {
      const api = mockApi({ [MARKET]: apiError(404, "NOT_FOUND", "Not found.") });

      open();

      expect(
        await screen.findByText("This page does not exist. Not even in the HG basement."),
      ).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Browse markets" })).toHaveAttribute("href", "/");
      expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
      // No error banner, no permission lecture, and nothing else is asked for.
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(api.calls(HISTORY)).toHaveLength(0);
    });

    test("an id that cannot be a market's is the same screen", async () => {
      mockApi({
        [MARKET]: apiError(422, "VALIDATION_ERROR", "The request is not valid.", {
          errors: [{ loc: ["path", "market_id"], msg: "Input should be a valid UUID" }],
        }),
      });

      renderWithProviders(<MarketDetailScreen marketId="not-an-id" />);

      expect(
        await screen.findByText("This page does not exist. Not even in the HG basement."),
      ).toBeInTheDocument();
      expect(screen.queryByText("The request is not valid.")).not.toBeInTheDocument();
    });
  });

  describe("error", () => {
    test("shows the API's message in a banner, and reads again on Try again", async () => {
      const api = mockApi({
        [MARKET]: apiError(500, "INTERNAL_ERROR", "Something went wrong on our side."),
        [HISTORY]: json(200, history()),
      });
      const { user } = open();

      const banner = await screen.findByRole("alert");
      expect(banner).toHaveTextContent("Something went wrong on our side.");
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Market");
      api.on(MARKET, json(200, marketDetail()));

      await user.click(within(banner).getByRole("button", { name: "Try again" }));

      expect(await screen.findByRole("heading", { level: 1, name: QUESTION })).toBeInTheDocument();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    test("without a connection it says so", async () => {
      mockApi({ [MARKET]: noAnswer });

      open();

      expect(await screen.findByRole("alert")).toHaveTextContent("No connection. Try again.");
    });

    test("an error page of the proxy counts as temporarily unavailable", async () => {
      mockApi({ [MARKET]: errorPage(502) });

      open();

      expect(await screen.findByRole("alert")).toHaveTextContent(
        "This is temporarily unavailable. Try again shortly.",
      );
    });

    test("after RATE_LIMITED, Try again waits for the time the API names", async () => {
      mockApi({
        [MARKET]: apiError(429, "RATE_LIMITED", "Too many requests. Try again shortly.", {
          retry_after_seconds: 9,
        }),
      });

      open();

      const banner = await screen.findByRole("alert");
      expect(banner).toHaveTextContent("Too many requests. Try again shortly.");
      expect(within(banner).getByRole("button", { name: /Try again in \d+ s/ })).toBeDisabled();
    });

    test("FORBIDDEN shows the API's message as a banner", async () => {
      mockApi({
        [MARKET]: apiError(403, "FORBIDDEN", "Your account is suspended."),
        "GET /api/me": json(403, { code: "FORBIDDEN", message: "Your account is suspended." }),
      });

      open();

      expect(await screen.findByRole("alert")).toHaveTextContent("Your account is suspended.");
    });

    test("a history that cannot be read has its banner where the chart stands; the market stays", async () => {
      const api = mockApi({
        [MARKET]: json(200, marketDetail()),
        [HISTORY]: apiError(503, "SERVICE_UNAVAILABLE", "This is temporarily unavailable."),
      });
      const { user } = open();

      const banner = await screen.findByRole("alert");
      expect(banner).toHaveTextContent("This is temporarily unavailable.");
      expect(screen.getByRole("heading", { level: 1, name: QUESTION })).toBeInTheDocument();
      expect(button("YES 54.8%")).toBeEnabled();
      api.on(HISTORY, json(200, history()));

      await user.click(within(banner).getByRole("button", { name: "Try again" }));

      expect(await screen.findByRole("application")).toBeInTheDocument();
    });

    test("a market on screen stays there when reading it again fails", async () => {
      const api = mockMarketApi();
      open();
      await screen.findByRole("heading", { name: QUESTION });
      api.on(MARKET, noAnswer);
      api.on(HISTORY, noAnswer);
      api.on("GET /api/me", noAnswer);

      act(() => {
        window.dispatchEvent(new Event("visibilitychange"));
      });

      await waitFor(() => expect(api.calls(MARKET).length).toBeGreaterThan(1));
      expect(screen.getByRole("heading", { name: QUESTION })).toBeInTheDocument();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });
  });

  describe("an open market", () => {
    test("shows the question as its one h1, in the h2 look, with the Open badge", async () => {
      mockMarketApi();

      open();

      const question = await screen.findByRole("heading", { level: 1, name: QUESTION });
      expect(question).toHaveClass("type-h2");
      expect(question).not.toHaveClass("truncate", "line-clamp-3");
      expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
      expect(screen.getByText("Open")).toHaveAttribute("data-slot", "badge");
      // No condition to state: no banner.
      expect(screen.queryByRole("status")).not.toBeInTheDocument();
    });

    test("shows the price in display size with its chance label, the volume and the deadline", async () => {
      mockMarketApi();

      open();

      const price = (await screen.findByText("54.8%")).parentElement!;
      expect(price).toHaveClass("type-display", "text-foreground");
      expect(screen.getByText("chance")).toBeInTheDocument();
      // For a screen reader, in a polite live region.
      expect(screen.getByText("YES 54.8 percent")).toHaveAttribute("aria-live", "polite");
      expect(screen.getByText("1,250.5")).toBeInTheDocument();
      expect(screen.getByText("Vol.")).toBeInTheDocument();
      expect(screen.getByText("Closes in 3 days")).toBeInTheDocument();
    });

    test("draws the chart from the history, labelled with its summary, which also stands as text", async () => {
      mockMarketApi();

      open();

      const chart = await screen.findByRole("application", {
        name: "YES probability over time, now 54.8 percent, opened at 50.0 percent",
      });
      expect(chart.querySelector(".recharts-area-curve")).toBeInTheDocument();
      expect(screen.getByText("Opened at 50.0%, now 54.8%.")).toBeInTheDocument();
    });

    test("offers YES on the left and NO on the right, each with the API's price", async () => {
      mockMarketApi(marketDetail({ p_yes: 0.548, p_no: 0.452 }));

      open();

      const yes = await screen.findByRole("button", { name: "YES 54.8%" });
      const no = button("NO 45.2%");
      expect(yes).toBeEnabled();
      expect(no).toBeEnabled();
      expect(yes).toHaveAttribute("data-variant", "yes");
      expect(no).toHaveAttribute("data-variant", "no");
      expect(yes.compareDocumentPosition(no) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    });

    test("shows p_no as the API sent it, not as what is left of p_yes", async () => {
      mockMarketApi(marketDetail({ p_yes: 0.548, p_no: 0.4 }));

      open();

      expect(await screen.findByRole("button", { name: "NO 40.0%" })).toBeInTheDocument();
    });

    test("has no position section for a viewer who never bought into the market", async () => {
      mockMarketApi();

      open();

      await screen.findByRole("heading", { name: QUESTION });
      expect(querySection("Your position")).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Sell" })).not.toBeInTheDocument();
    });

    test("lists the rules under their six labels, with the times in the timezone of /config", async () => {
      mockMarketApi();

      open();

      await screen.findByRole("heading", { name: QUESTION });
      const rules = section("Rules");
      const facts = [...rules.querySelectorAll("dt")].map((term) => [
        term.textContent,
        term.nextElementSibling?.textContent,
      ]);
      expect(facts).toEqual([
        ["YES means", "No interruption of more than 30 minutes is reported."],
        ["NO means", "At least one interruption of more than 30 minutes is reported."],
        [
          "How it is decided",
          "Decided from the operator's published service notices for the session.",
        ],
        ["Source", "polybahn.ch service notices"],
        ["Trading closes", "14 Oct 2026, 18:00"],
        ["Decided by", "21 Oct 2026, 18:00"],
      ]);
    });

    test("shows the void rule in full, where anyone can read it before trading", async () => {
      const rule =
        "If VIS voids this market, every trader gets back their net stake in it: what they paid for shares minus what they received for selling, never less than zero.";
      mockMarketApi(marketDetail({ void_rule: rule }));

      open();

      await screen.findByRole("heading", { name: QUESTION });
      const voided = section("If the market is voided");
      expect(within(voided).getByText(rule)).toBeVisible();
      expect(voided.querySelector("details, [aria-expanded], .truncate")).toBeNull();
      expect(querySection("Resolution")).not.toBeInTheDocument();
    });

    test("keeps the order of the screen: header, chart, buttons, rules, void rule", async () => {
      mockMarketApi(marketDetail({ viewer: marketViewerWithPosition() }));

      open();

      await screen.findByRole("application");
      const order = [
        screen.getByRole("heading", { level: 1 }),
        screen.getByText("chance"),
        document.querySelector("[data-slot=probability-chart]")!,
        button("YES 54.8%"),
        section("Your position"),
        section("Rules"),
        section("If the market is voided"),
      ];
      for (let index = 1; index < order.length; index += 1) {
        expect(
          order[index - 1]!.compareDocumentPosition(order[index]!) &
            Node.DOCUMENT_POSITION_FOLLOWING,
        ).toBeTruthy();
      }
    });
  });

  describe("your position", () => {
    test("names each holding with what it is worth, the stake, and what is left of the limit", async () => {
      mockMarketApi(marketDetail({ viewer: marketViewerWithPosition() }));

      open();

      await screen.findByRole("heading", { name: QUESTION });
      const position = section("Your position");
      const lines = [...position.querySelectorAll("dt")].map((term) => term.textContent);
      expect(lines).toEqual(["91.64 YES shares", "Your stake"]);
      // The value of the holding, cut after the second decimal and labelled.
      expect(within(position).getByText("50.22")).toBeInTheDocument();
      expect(within(position).getByText("Value")).toBeInTheDocument();
      expect(within(position).getByText("50 tokens")).toHaveClass("sr-only");
      expect(
        within(position).getByText("50 of 100 tokens left in this market"),
      ).toBeInTheDocument();
      expect(within(position).getByRole("button", { name: "Sell" })).toBeEnabled();
    });

    test("lists both outcomes when both are held, YES first as the API sends them", async () => {
      mockMarketApi(
        marketDetail({
          viewer: marketViewerWithPosition({
            holdings: holdings(
              { shares: "91.645", value_cr: "50.221461" },
              { shares: "1", value_cr: "0.452" },
            ),
          }),
        }),
      );

      open();

      await screen.findByRole("heading", { name: QUESTION });
      const lines = [...section("Your position").querySelectorAll("dt")].map(
        (term) => term.textContent,
      );
      expect(lines).toEqual(["91.64 YES shares", "1 NO share", "Your stake"]);
    });

    test("says so when everything was sold, and Sell follows the allowed flags", async () => {
      mockMarketApi(
        marketDetail({
          viewer: marketViewer({ cost_cr: "50", proceeds_cr: "48.5", net_stake_cr: "1.5" }),
        }),
      );

      open();

      await screen.findByRole("heading", { name: QUESTION });
      const position = section("Your position");
      expect(within(position).getByText("You hold no shares.")).toBeInTheDocument();
      expect(within(position).getByText("Your stake")).toBeInTheDocument();
      expect(within(position).getByText("1.5")).toBeInTheDocument();
      expect(within(position).getByRole("button", { name: "Sell" })).toBeDisabled();
    });

    test("is decided by cost_cr being the string 0, not by a comparison", async () => {
      // Everything was sold for more than it cost: the stake is 0, the position is still shown.
      mockMarketApi(
        marketDetail({
          viewer: marketViewer({ cost_cr: "0.000001", proceeds_cr: "60", net_stake_cr: "0" }),
        }),
      );

      open();

      await screen.findByRole("heading", { name: QUESTION });
      expect(section("Your position")).toBeInTheDocument();
    });

    test("an amount stays the string it arrived as: figures are cut, never rounded", async () => {
      mockMarketApi(
        marketDetail({
          volume_cr: "1999.999999",
          viewer: marketViewerWithPosition({
            net_stake_cr: "49.999999",
            stake_left_cr: "50.000001",
            holdings: holdings({ shares: "91.645999", value_cr: "50.229999" }),
          }),
        }),
      );

      open();

      await screen.findByRole("heading", { name: QUESTION });
      // As numbers these would have shown as 2,000, 91.65, 50.23, 50 and 50.
      expect(screen.getByText("1,999.99")).toBeInTheDocument();
      const position = section("Your position");
      expect(within(position).getByText("91.64 YES shares")).toBeInTheDocument();
      expect(within(position).getByText("50.22")).toBeInTheDocument();
      expect(within(position).getByText("49.99")).toBeInTheDocument();
      expect(
        within(position).getByText("50.00 of 100 tokens left in this market"),
      ).toBeInTheDocument();
    });
  });

  describe("states by status", () => {
    test("OPEN with OWN_MARKET: the buttons are disabled and a neutral banner says why", async () => {
      mockMarketApi(
        marketDetail({
          viewer: marketViewer({ can_trade: false, cannot_trade_reason: "OWN_MARKET" }),
        }),
      );

      open();

      await loaded();
      const banner = screen.getByRole("status");
      expect(banner).toHaveTextContent("You proposed this market, so you cannot trade it.");
      expect(banner).toHaveClass("bg-raised");
      expect(button("YES 54.8%")).toBeDisabled();
      expect(button("NO 45.2%")).toBeDisabled();
      expect(screen.getByText("Open")).toBeInTheDocument();
    });

    test("HALTED: trading is paused, the buttons and Sell are disabled, chart and position stay", async () => {
      mockMarketApi(
        marketDetail({
          status: "HALTED",
          trading_open: false,
          viewer: marketViewerWithPosition({
            can_trade: false,
            cannot_trade_reason: "MARKET_NOT_OPEN",
            stake_presets: [{ amount_cr: "25", allowed: false }],
            sell_presets: [
              { outcome: "YES", portion: "HALF", shares: "45.8225", allowed: false },
              { outcome: "YES", portion: "ALL", shares: "91.645", allowed: false },
              { outcome: "NO", portion: "HALF", shares: "0", allowed: false },
              { outcome: "NO", portion: "ALL", shares: "0", allowed: false },
            ],
          }),
        }),
      );

      open();

      await loaded();
      const banners = screen.getAllByRole("status");
      expect(banners).toHaveLength(1);
      expect(banners[0]).toHaveTextContent("Trading is paused.");
      expect(screen.getByText("Paused")).toHaveAttribute("data-slot", "badge");
      expect(button("YES 54.8%")).toBeDisabled();
      expect(button("NO 45.2%")).toBeDisabled();
      expect(button("Sell")).toBeDisabled();
      expect(await screen.findByRole("application")).toBeInTheDocument();
      expect(within(section("Your position")).getByText("91.64 YES shares")).toBeInTheDocument();
      // The deadline of a market that is not open for trading is its date alone.
      expect(screen.getByText("14 Oct 2026")).toBeInTheDocument();
    });

    test("CLOSED: trading is over, with the one campus line of the screen and no buttons", async () => {
      mockMarketApi(closed({ viewer: marketViewerWithPosition({ can_trade: false }) }));

      open();

      await loaded();
      const banner = screen.getByRole("status");
      expect(banner).toHaveTextContent("Trading is over. Now we wait, like for exam results.");
      expect(banner.querySelector("svg")).toBeInTheDocument();
      expect(screen.getByText("Closed")).toHaveAttribute("data-slot", "badge");
      expect(screen.queryByRole("button", { name: /^YES/ })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /^NO/ })).not.toBeInTheDocument();
      // The position stays, without what belongs to trading.
      const position = section("Your position");
      expect(within(position).getByText("Your stake")).toBeInTheDocument();
      expect(within(position).queryByRole("button", { name: "Sell" })).not.toBeInTheDocument();
      expect(within(position).queryByText(/left in this market/)).not.toBeInTheDocument();
      expect(screen.getByText("54.8%")).toBeInTheDocument();
    });

    test("RESOLVED: the outcome replaces the price, Paid out replaces Your stake, and the resolution is shown", async () => {
      mockMarketApi(
        closed({
          status: "RESOLVED",
          outcome: "YES",
          p_yes: 0.81,
          p_no: 0.19,
          resolution: {
            outcome: "YES",
            evidence: "The operator reported no interruption for the whole session.",
            resolved_at: "2026-10-20T09:30:00Z",
          },
          viewer: marketViewerWithPosition({
            can_trade: false,
            cannot_trade_reason: "MARKET_NOT_OPEN",
            payout_cr: "91.645",
            holdings: holdings({ shares: "91.645", value_cr: "91.645" }),
          }),
        }),
      );

      open();

      await screen.findByRole("heading", { name: QUESTION });
      const outcome = document.querySelector("[data-slot=price]")!;
      expect(outcome).toHaveTextContent("Resolved YES");
      expect(outcome).toHaveClass("type-display", "text-yes");
      expect(screen.queryByText("chance")).not.toBeInTheDocument();
      expect(screen.queryByText("81.0%")).not.toBeInTheDocument();
      // No banner for a resolved market, and nothing to trade.
      expect(screen.queryByRole("status")).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /^YES/ })).not.toBeInTheDocument();

      const position = section("Your position");
      const lines = [...position.querySelectorAll("dt")].map((term) => term.textContent);
      expect(lines).toEqual(["91.64 YES shares", "Paid out"]);
      // A payout carries its sign, in the green of a gain.
      const paid = within(position).getByText("+91.64 tokens").closest("[data-slot=amount]")!;
      expect(paid).toHaveClass("text-yes-text");
      expect(paid).toHaveTextContent("+");

      const resolution = section("Resolution");
      expect(within(resolution).getByText("Resolved YES")).toHaveAttribute("data-slot", "badge");
      expect(
        within(resolution).getByText(
          "The operator reported no interruption for the whole session.",
        ),
      ).toBeInTheDocument();
      expect(within(resolution).getByText("20 Oct 2026, 11:30")).toBeInTheDocument();
    });

    test("RESOLVED against the viewer: a payout of nothing has no sign and no green", async () => {
      mockMarketApi(
        closed({
          status: "RESOLVED",
          outcome: "NO",
          resolution: {
            outcome: "NO",
            evidence: "It stopped.",
            resolved_at: "2026-10-20T09:30:00Z",
          },
          viewer: marketViewerWithPosition({
            can_trade: false,
            payout_cr: "0",
            holdings: holdings({ shares: "91.645", value_cr: "0" }),
          }),
        }),
      );

      open();

      await screen.findByRole("heading", { name: QUESTION });
      expect(document.querySelector("[data-slot=price]")).toHaveClass("text-no");
      const position = section("Your position");
      const paid = within(position).getAllByText("0 tokens").at(-1)!.closest("[data-slot=amount]")!;
      expect(paid).toHaveClass("text-foreground");
      expect(paid).not.toHaveTextContent("+");
      expect(within(section("Resolution")).getByText("Resolved NO")).toBeInTheDocument();
    });

    test("VOID: a neutral banner, Refunded in place of Your stake, and the reason VIS gave", async () => {
      mockMarketApi(
        closed({
          status: "VOID",
          outcome: "VOID",
          resolution: {
            outcome: "VOID",
            evidence: "The operator stopped publishing service notices.",
            resolved_at: "2026-10-20T09:30:00Z",
          },
          viewer: marketViewer({
            can_trade: false,
            cannot_trade_reason: "MARKET_NOT_OPEN",
            cost_cr: "50",
            net_stake_cr: "50",
            refund_cr: "50",
          }),
        }),
      );

      open();

      await loaded();
      const banner = screen.getByRole("status");
      expect(banner).toHaveTextContent("This market was voided. Stakes were refunded.");
      expect(banner).toHaveClass("bg-raised");

      const position = section("Your position");
      expect(within(position).getByText("You hold no shares.")).toBeInTheDocument();
      expect([...position.querySelectorAll("dt")].map((term) => term.textContent)).toEqual([
        "Refunded",
      ]);
      expect(within(position).getByText("50 tokens")).toBeInTheDocument();

      const resolution = section("Resolution");
      expect(within(resolution).getByText("Voided")).toHaveAttribute("data-slot", "badge");
      expect(
        within(resolution).getByText("The operator stopped publishing service notices."),
      ).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /^YES/ })).not.toBeInTheDocument();
    });

    test("green and red stay with the outcome: no status banner or badge of an unresolved market uses them", async () => {
      mockMarketApi(closed());

      open();

      await loaded();
      const banner = screen.getByRole("status");
      expect(banner.className).not.toMatch(/yes|bg-no|text-no/);
      expect(screen.getByText("Closed").className).not.toMatch(/yes|bg-no|text-no/);
    });

    test.each([
      ["PROPOSED", "In review"],
      ["REJECTED", "Not approved"],
      ["EXPIRED", "Expired"],
    ] as const)(
      "%s: the badge and the rules, and nothing that belongs to trading",
      async (status, badge) => {
        const streams = installFakeEventSource();
        const api = mockApi({
          [MARKET]: json(
            200,
            closed({
              status,
              tier: null,
              opened_at: null,
              rejection_reason: status === "REJECTED" ? "The source is not public." : null,
            }),
          ),
        });

        open();

        expect(
          await screen.findByRole("heading", { level: 1, name: QUESTION }),
        ).toBeInTheDocument();
        expect(screen.getByText(badge)).toHaveAttribute("data-slot", "badge");
        expect(section("Rules")).toBeInTheDocument();
        // No price, chart, outcome buttons, position, void rule or banner.
        expect(document.querySelector("[data-slot=price]")).not.toBeInTheDocument();
        expect(screen.queryByText("chance")).not.toBeInTheDocument();
        expect(screen.queryByText("Vol.")).not.toBeInTheDocument();
        expect(document.querySelector("[data-slot=probability-chart]")).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: /^(YES|NO)/ })).not.toBeInTheDocument();
        expect(querySection("Your position")).not.toBeInTheDocument();
        expect(querySection("If the market is voided")).not.toBeInTheDocument();
        expect(screen.queryByRole("status")).not.toBeInTheDocument();
        // And the screen asks for no history and no stream.
        expect(api.calls(HISTORY)).toHaveLength(0);
        expect(streams.instances).toHaveLength(0);
      },
    );

    test("REJECTED: shows the reason VIS gave", async () => {
      mockApi({
        [MARKET]: json(
          200,
          closed({ status: "REJECTED", rejection_reason: "The source is not public." }),
        ),
      });

      open();

      await screen.findByRole("heading", { name: QUESTION });
      expect(within(section("Reason")).getByText("The source is not public.")).toBeInTheDocument();
    });

    test("PROPOSED: has no reason to show", async () => {
      mockApi({ [MARKET]: json(200, closed({ status: "PROPOSED" })) });

      open();

      await screen.findByRole("heading", { name: QUESTION });
      expect(querySection("Reason")).not.toBeInTheDocument();
    });

    test("EXPIRED: a proposal nobody reviewed before its close time, with a quiet badge and no reason", async () => {
      mockApi({
        [MARKET]: json(
          200,
          closed({ status: "EXPIRED", opened_at: null, close_at: "2026-10-09T16:00:00Z" }),
        ),
      });

      open();

      await screen.findByRole("heading", { level: 1, name: QUESTION });
      const badge = screen.getByText("Expired");
      expect(badge).toHaveAttribute("data-slot", "badge");
      // A finished state, like "Closed" and "Not approved": no colour of an outcome, no icon.
      expect(badge).toHaveClass("bg-raised", "text-muted-foreground");
      expect(badge.className).not.toMatch(/yes|bg-no|text-no/);
      expect(badge.querySelector("svg")).not.toBeInTheDocument();
      expect(querySection("Reason")).not.toBeInTheDocument();
      // Its rules stay readable, with the close time that passed.
      const rules = section("Rules");
      expect(within(rules).getByText("9 Oct 2026, 18:00")).toBeInTheDocument();
      // No banner speaks for it, and it is no error.
      expect(screen.queryByRole("status")).not.toBeInTheDocument();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });
  });

  describe("how the market is priced is VIS's business", () => {
    test("shows no tier, no liquidity figure and no loss bound, also to an admin whose answer carries them", async () => {
      // The API sends the three to admins: the admin area shows them, this screen does not.
      mockMarketApi(
        adminMarketDetail({
          tier: "MEDIUM",
          b_cr: "2000",
          max_loss_cr: "1386.294362",
          void_cost_cr: "-130.752819",
          viewer: marketViewerWithPosition({ can_settle: true }),
        }),
      );

      open();

      await screen.findByRole("application");
      const text = screen.getByRole("main").textContent ?? "";
      expect(text).not.toMatch(/tier|liquidity|loss|budget|medium/i);
      expect(text).not.toContain("2,000");
      expect(text).not.toContain("1,386");
      expect(text).not.toContain("130.75");
      // Nor who proposed it.
      expect(text).not.toContain("bea.beispiel@ethz.ch");
    });

    test("the sections of the screen are the ones the design names, and none is about pricing", async () => {
      mockMarketApi(adminMarketDetail({ viewer: marketViewerWithPosition() }));

      open();

      await screen.findByRole("application");
      const headings = within(screen.getByRole("main"))
        .getAllByRole("heading", { level: 2 })
        .map((heading) => heading.textContent);
      expect(headings).toEqual(["Your position", "Rules", "If the market is voided"]);
    });
  });

  describe("the chart's right edge", () => {
    test("is until of the history's answer, not the clock of the device", async () => {
      // The device stands on 11 October; the server's now, as the answer says it, is the 4th.
      mockMarketApi(marketDetail(), history({ until: "2026-10-04T10:00:00Z" }));

      open();

      const chart = await screen.findByRole("application");
      const dates = [...chart.querySelectorAll(".recharts-xAxis-tick-labels text")].map(
        (label) => label.textContent,
      );
      expect(dates).toEqual(["1 Oct 2026", "4 Oct 2026"]);
    });

    test("for a market whose trading is over it is the moment the answer names", async () => {
      mockMarketApi(
        closed({ close_at: "2026-10-09T16:00:00Z" }),
        history({ until: "2026-10-09T16:00:00Z" }),
      );

      open();

      const chart = await screen.findByRole("application");
      const dates = [...chart.querySelectorAll(".recharts-xAxis-tick-labels text")].map(
        (label) => label.textContent,
      );
      expect(dates).toEqual(["1 Oct 2026", "9 Oct 2026"]);
    });
  });

  describe("requests", () => {
    test("reads the market and its history from the API on its own origin, as JSON", async () => {
      const api = mockMarketApi();

      open();
      await screen.findByRole("application");

      expect(api.requests.map((request) => `${request.method} ${request.path}`)).toEqual([
        `GET /api/markets/${MARKET_ID}`,
        `GET /api/markets/${MARKET_ID}/history`,
      ]);
      for (const request of api.requests) {
        expect(new URL(request.url).origin).toBe(window.location.origin);
        expect(request.headers.get("Accept")).toBe("application/json");
        expect([...request.query.keys()]).toEqual([]);
      }
    });

    test("writes nothing: opening a market sends no request that changes anything", async () => {
      const api = mockMarketApi(marketDetail({ viewer: marketViewerWithPosition() }));

      open();
      await screen.findByRole("application");

      expect(api.requests.every((request) => request.method === "GET")).toBe(true);
    });

    test("reads the market again when the window gets the focus back", async () => {
      const api = mockMarketApi();
      api.on("GET /api/me", noAnswer);
      open();
      await screen.findByRole("application");
      api.on(MARKET, json(200, marketDetail({ p_yes: 0.6, p_no: 0.4, version: 9 })));

      act(() => {
        window.dispatchEvent(new Event("visibilitychange"));
      });

      expect(await screen.findByText("60.0%")).toBeInTheDocument();
      expect(api.calls(HISTORY)).toHaveLength(2);
    });
  });
});
