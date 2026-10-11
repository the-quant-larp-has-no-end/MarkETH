import { act, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { AppShell } from "@/components/shell";
import { sizeCharts } from "@/test/charts";
import { installFakeEventSource } from "@/test/fake-event-source";
import {
  holdings,
  MARKET_ID,
  marketDetail,
  marketViewer,
  marketViewerWithPosition,
  statusEvent,
  TODAY,
} from "@/test/fixtures";
import { deferred, json, mockApi } from "@/test/http";
import { setViewport } from "@/test/media";
import { followHistoryWrites, navigation } from "@/test/navigation";
import { watchScrolling } from "@/test/observers";
import { renderWithProviders } from "@/test/render";

import { MarketDetailScreen } from "./index";
import {
  answerTrade,
  followsTicket,
  HISTORY,
  MARKET,
  mockMarketApi,
  PATH,
  resetTrade,
  sendTrade,
} from "./testing";

vi.mock("@/features/trade", async () => (await import("./testing")).tradeFeature);

const QUESTION = "Will the Polybahn run without interruption during the exam session?";

type Market = ReturnType<typeof marketDetail>;

let writes: ReturnType<typeof followHistoryWrites>;

/**
 * The screen with its market read: its h1 is the question. It is looked for as text:
 * behind an open sheet the page is hidden from assistive technology, and so from a query
 * by role. The surface of the ticket has the question too, as its header.
 */
async function open(market: Market = marketDetail()) {
  const api = mockMarketApi(market);
  const view = renderWithProviders(<MarketDetailScreen marketId={MARKET_ID} />);
  await screen.findByText(QUESTION, { selector: "h1" });
  return { api, ...view };
}

const sheet = () => screen.findByRole("dialog", { name: "Trade" });
const noSheet = () => expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
const ticket = () => screen.getByTestId("ticket");
const button = (name: string | RegExp) => screen.getByRole("button", { name });

/** A viewer who holds NO shares only. */
const holdingNo = () =>
  marketViewerWithPosition({
    holdings: holdings({}, { shares: "20", value_cr: "9.04" }),
    sell_presets: [
      { outcome: "YES", portion: "HALF", shares: "0", proceeds_cr: "0", allowed: false },
      { outcome: "YES", portion: "ALL", shares: "0", proceeds_cr: "0", allowed: false },
      { outcome: "NO", portion: "HALF", shares: "10", proceeds_cr: "5.366205", allowed: true },
      { outcome: "NO", portion: "ALL", shares: "20", proceeds_cr: "10.73241", allowed: true },
    ],
  });

const halted = (viewer = marketViewer()) =>
  marketDetail({
    status: "HALTED",
    trading_open: false,
    viewer: { ...viewer, can_trade: false, cannot_trade_reason: "MARKET_NOT_OPEN" },
  });

beforeEach(() => {
  navigation.go(PATH);
  writes = followHistoryWrites();
  sizeCharts();
  resetTrade();
  vi.useFakeTimers({ now: TODAY, toFake: ["Date"] });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("MarketDetailScreen and the trade ticket", () => {
  describe("on a phone, the ticket is a bottom sheet that the address opens", () => {
    test("an outcome button opens the sheet through the address, on Buy and that outcome", async () => {
      const { user } = await open();
      noSheet();

      await user.click(button("YES 54.8%"));

      expect(navigation.href).toBe(`${PATH}?buy=YES`);
      const dialog = await sheet();
      expect(dialog).toHaveAttribute("data-slot", "drawer-content");
      expect(within(dialog).getByText("Ticket: BUY YES")).toBeInTheDocument();
    });

    test("NO opens it on NO", async () => {
      const { user } = await open();

      await user.click(button("NO 45.2%"));

      expect(navigation.href).toBe(`${PATH}?buy=NO`);
      expect(within(await sheet()).getByText("Ticket: BUY NO")).toBeInTheDocument();
    });

    test("opening is a step in the history, written without asking the server for the route", async () => {
      const { user } = await open();

      await user.click(button("YES 54.8%"));

      expect(writes.pushState).toHaveBeenCalledTimes(1);
      expect(writes.pushState).toHaveBeenCalledWith(null, "", `${PATH}?buy=YES`);
      expect(navigation.depth).toBe(1);
    });

    test("the sheet's title names it for a screen reader without showing", async () => {
      const { user } = await open();

      await user.click(button("YES 54.8%"));

      await sheet();
      expect(screen.getByRole("heading", { name: "Trade" })).toHaveClass("sr-only");
    });

    test("hands the ticket the market as the API sent it", async () => {
      const { user } = await open(
        marketDetail({ viewer: marketViewer({ balance_cr: "240.000001" }) }),
      );

      await user.click(button("YES 54.8%"));

      await sheet();
      expect(screen.getByText("Ticket balance: 240.000001")).toBeInTheDocument();
      expect(screen.getByText("Ticket market: OPEN")).toBeInTheDocument();
    });

    test("closing takes the step back: the market stays on screen and the way back is as before", async () => {
      const { user } = await open();
      await user.click(button("YES 54.8%"));
      await sheet();

      await user.click(screen.getByRole("button", { name: "Close" }));

      await waitFor(noSheet);
      expect(navigation.href).toBe(PATH);
      expect(navigation.depth).toBe(0);
      expect(navigation.router.back).toHaveBeenCalledTimes(1);
      expect(screen.getByRole("heading", { level: 1, name: QUESTION })).toBeInTheDocument();
    });

    test("Escape closes it", async () => {
      const { user } = await open();
      await user.click(button("YES 54.8%"));
      await sheet();

      await user.keyboard("{Escape}");

      await waitFor(noSheet);
      expect(navigation.href).toBe(PATH);
    });

    test("the phone's Back closes the sheet and leaves the market open", async () => {
      const { user } = await open();
      await user.click(button("NO 45.2%"));
      await sheet();

      act(() => navigation.router.back());

      await waitFor(noSheet);
      expect(navigation.pathname).toBe(PATH);
      expect(screen.getByRole("heading", { level: 1, name: QUESTION })).toBeInTheDocument();
    });

    test("can be opened again after it was closed", async () => {
      const { user } = await open();
      await user.click(button("YES 54.8%"));
      await sheet();
      await user.keyboard("{Escape}");
      await waitFor(noSheet);

      await user.click(button("NO 45.2%"));

      expect(within(await sheet()).getByText("Ticket: BUY NO")).toBeInTheDocument();
      expect(navigation.depth).toBe(1);
    });

    test("Sell opens it on Sell and the outcome that can be sold", async () => {
      const { user } = await open(marketDetail({ viewer: marketViewerWithPosition() }));

      await user.click(button("Sell"));

      expect(navigation.href).toBe(`${PATH}?sell=YES`);
      expect(within(await sheet()).getByText("Ticket: SELL YES")).toBeInTheDocument();
    });

    test("Sell opens on NO for a viewer who holds NO only", async () => {
      const { user } = await open(marketDetail({ viewer: holdingNo() }));

      await user.click(button("Sell"));

      expect(navigation.href).toBe(`${PATH}?sell=NO`);
      expect(within(await sheet()).getByText("Ticket: SELL NO")).toBeInTheDocument();
    });

    test("the address follows the ticket when side or outcome change inside it, without another step", async () => {
      const { user } = await open(marketDetail({ viewer: marketViewerWithPosition() }));
      await user.click(button("YES 54.8%"));
      await sheet();

      await user.click(button("Ticket to sell NO"));

      expect(navigation.href).toBe(`${PATH}?sell=NO`);
      expect(screen.getByText("Ticket: SELL NO")).toBeInTheDocument();
      expect(navigation.depth).toBe(1);
      expect(writes.pushState).toHaveBeenCalledTimes(1);

      // Closing still takes the one step back.
      await user.keyboard("{Escape}");
      await waitFor(noSheet);
      expect(navigation.href).toBe(PATH);
      expect(navigation.depth).toBe(0);
    });

    test("closes when the ticket says it is done", async () => {
      const { user } = await open();
      await user.click(button("YES 54.8%"));
      await sheet();

      await user.click(button("Ticket done"));

      await waitFor(noSheet);
      expect(navigation.href).toBe(PATH);
    });

    test("stays open when the market is paused behind it: only the user or the ticket closes it", async () => {
      const streams = installFakeEventSource();
      const { api, user } = await open();
      act(() => streams.latest().open());
      await user.click(button("YES 54.8%"));
      await sheet();
      api.on(MARKET, json(200, halted()));

      act(() => streams.latest().emit("status", statusEvent({ status: "HALTED" })));

      // The ticket is told, and stays where the user can read what it says.
      expect(await screen.findByText("Ticket market: HALTED")).toBeInTheDocument();
      expect(screen.getByRole("dialog", { name: "Trade" })).toBeInTheDocument();
      expect(navigation.href).toBe(`${PATH}?buy=YES`);
    });

    test("hides the tab bar while the sheet is open", async () => {
      mockMarketApi();
      const { user } = renderWithProviders(
        <AppShell>
          <MarketDetailScreen marketId={MARKET_ID} />
        </AppShell>,
      );
      await screen.findByRole("heading", { level: 1, name: QUESTION });
      expect(screen.getByRole("navigation", { name: "Main" })).toBeInTheDocument();

      await user.click(button("YES 54.8%"));
      await sheet();
      expect(screen.queryByRole("navigation", { name: "Main" })).not.toBeInTheDocument();

      await user.keyboard("{Escape}");
      await waitFor(noSheet);
      expect(screen.getByRole("navigation", { name: "Main" })).toBeInTheDocument();
    });
  });

  describe("an address that asks for a trade", () => {
    test.each([
      ["buy=YES", "BUY YES"],
      ["buy=NO", "BUY NO"],
    ])("?%s opens the sheet on that side and outcome", async (query, shown) => {
      navigation.go(`${PATH}?${query}`);

      await open();

      expect(within(await sheet()).getByText(`Ticket: ${shown}`)).toBeInTheDocument();
      expect(navigation.href).toBe(`${PATH}?${query}`);
      expect(writes.pushState).not.toHaveBeenCalled();
    });

    test("?sell=YES opens it on Sell for a viewer who can sell YES", async () => {
      navigation.go(`${PATH}?sell=YES`);

      await open(marketDetail({ viewer: marketViewerWithPosition() }));

      expect(within(await sheet()).getByText("Ticket: SELL YES")).toBeInTheDocument();
    });

    test("waits for the market: nothing opens before it is known what the viewer can do", async () => {
      navigation.go(`${PATH}?buy=YES`);
      const answer = deferred();
      mockApi({
        [MARKET]: answer.handler,
        [HISTORY]: json(200, { source: "postgres", points: [] }),
      });

      renderWithProviders(<MarketDetailScreen marketId={MARKET_ID} />);
      await screen.findByText("Loading market");
      noSheet();
      expect(navigation.href).toBe(`${PATH}?buy=YES`);

      await act(async () => answer.resolve(json(200, marketDetail())));

      expect(within(await sheet()).getByText("Ticket: BUY YES")).toBeInTheDocument();
    });

    test("closing a sheet the address arrived with takes the request out of it, and the way back still leads where it led", async () => {
      navigation.go(`${PATH}?buy=NO`);
      const { user } = await open();
      await sheet();

      await user.click(screen.getByRole("button", { name: "Close" }));

      await waitFor(noSheet);
      expect(navigation.href).toBe(PATH);
      expect(writes.replaceState).toHaveBeenLastCalledWith(null, "", PATH);
      expect(navigation.router.back).not.toHaveBeenCalled();
      expect(navigation.depth).toBe(0);
    });

    test.each([
      ["a paused market", () => halted()],
      [
        "the proposer's own market",
        () =>
          marketDetail({
            viewer: marketViewer({ can_trade: false, cannot_trade_reason: "OWN_MARKET" }),
          }),
      ],
      [
        "a closed market",
        () =>
          marketDetail({
            status: "CLOSED",
            trading_open: false,
            viewer: marketViewer({ can_trade: false, cannot_trade_reason: "MARKET_NOT_OPEN" }),
          }),
      ],
    ])("a buy in %s opens the market without the sheet", async (_name, market) => {
      navigation.go(`${PATH}?buy=YES`);

      await open(market());

      // The request leaves the address, so that it cannot open a sheet by itself later.
      await waitFor(() => expect(navigation.href).toBe(PATH));
      noSheet();
      expect(writes.pushState).not.toHaveBeenCalled();
    });

    test("a sell with no preset that is allowed opens the market without the sheet", async () => {
      navigation.go(`${PATH}?sell=YES`);

      await open();

      await waitFor(() => expect(navigation.href).toBe(PATH));
      noSheet();
    });

    test("a sell of an outcome the viewer does not hold shows the one that can be sold", async () => {
      navigation.go(`${PATH}?sell=NO`);

      await open(marketDetail({ viewer: marketViewerWithPosition() }));

      expect(within(await sheet()).getByText("Ticket: SELL YES")).toBeInTheDocument();
      expect(navigation.href).toBe(`${PATH}?sell=YES`);
    });

    test("a request that became impossible does not open the sheet later by itself", async () => {
      const streams = installFakeEventSource();
      navigation.go(`${PATH}?buy=YES`);
      const { api } = await open(halted());
      await waitFor(() => expect(navigation.href).toBe(PATH));
      act(() => streams.latest().open());
      api.on(MARKET, json(200, marketDetail()));

      act(() =>
        streams.latest().emit("status", statusEvent({ status: "OPEN", trading_open: true })),
      );

      await waitFor(() => expect(button("YES 54.8%")).toBeEnabled());
      noSheet();
    });

    test("anything else in the address asks for nothing", async () => {
      navigation.go(`${PATH}?buy=MAYBE&sell=all`);

      await open();

      noSheet();
      expect(navigation.href).toBe(`${PATH}?buy=MAYBE&sell=all`);
    });

    test("keeps what else the address holds", async () => {
      navigation.go(`${PATH}?from=portfolio`);
      const { user } = await open();

      await user.click(button("YES 54.8%"));
      expect(navigation.href).toBe(`${PATH}?from=portfolio&buy=YES`);

      await user.click(button("Ticket to sell NO"));
      expect(navigation.href).toBe(`${PATH}?from=portfolio&sell=NO`);
    });
  });

  describe("from 768 px", () => {
    beforeEach(() => setViewport("wide"));

    test("the sheet is a dialog", async () => {
      const { user } = await open();

      await user.click(button("YES 54.8%"));

      const dialog = await sheet();
      expect(dialog).toHaveAttribute("data-slot", "dialog-content");
      expect(within(dialog).getByText("Ticket: BUY YES")).toBeInTheDocument();
      expect(navigation.href).toBe(`${PATH}?buy=YES`);
    });

    test("there is no panel yet, and the content keeps to its 720 px column", async () => {
      await open();

      expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
      expect(screen.getByRole("main")).toHaveClass("md:max-w-[45rem]");
      expect(screen.getByRole("main")).not.toHaveClass("lg:max-w-[67.5rem]");
    });
  });

  describe("from 1024 px, the ticket is a sticky panel beside the content", () => {
    beforeEach(() => setViewport("desktop"));

    const panel = () => screen.getByRole("complementary", { name: "Trade" });

    test("is in place whenever the outcome buttons are shown, and starts on Buy and YES", async () => {
      await open();

      expect(within(panel()).getByText("Ticket: BUY YES")).toBeInTheDocument();
      expect(panel()).toHaveClass("sticky", "bg-card", "rounded-lg", "border");
      expect(button("YES 54.8%")).toBeInTheDocument();
      noSheet();
      expect(navigation.href).toBe(PATH);
    });

    test("stands in a 360 px column beside the content, which may then be 1080 px wide", async () => {
      await open();

      expect(panel().parentElement).toHaveClass("grid", "grid-cols-[minmax(0,1fr)_22.5rem]");
      expect(screen.getByRole("main")).toHaveClass("lg:max-w-[67.5rem]");
    });

    test("an outcome button sets the panel through the address, without a step in the history and without a sheet", async () => {
      const { user } = await open();

      await user.click(button("NO 45.2%"));

      expect(navigation.href).toBe(`${PATH}?buy=NO`);
      expect(within(panel()).getByText("Ticket: BUY NO")).toBeInTheDocument();
      expect(writes.pushState).not.toHaveBeenCalled();
      expect(navigation.depth).toBe(0);
      noSheet();
    });

    test("an address that asks for a trade sets the panel", async () => {
      navigation.go(`${PATH}?sell=YES`);

      await open(marketDetail({ viewer: marketViewerWithPosition() }));

      expect(within(panel()).getByText("Ticket: SELL YES")).toBeInTheDocument();
      noSheet();
    });

    test("Sell sets the panel to Sell", async () => {
      const { user } = await open(marketDetail({ viewer: marketViewerWithPosition() }));

      await user.click(button("Sell"));

      expect(navigation.href).toBe(`${PATH}?sell=YES`);
      expect(within(panel()).getByText("Ticket: SELL YES")).toBeInTheDocument();
    });

    test("the address follows the ticket, and Done starts over on Buy and YES", async () => {
      const { user } = await open(marketDetail({ viewer: holdingNo() }));

      await user.click(button("Ticket to sell NO"));
      expect(navigation.href).toBe(`${PATH}?sell=NO`);
      expect(within(panel()).getByText("Ticket: SELL NO")).toBeInTheDocument();

      await user.click(button("Ticket done"));
      expect(navigation.href).toBe(PATH);
      expect(within(panel()).getByText("Ticket: BUY YES")).toBeInTheDocument();
      expect(navigation.router.back).not.toHaveBeenCalled();
    });

    test("stays on what the ticket was set to when the market changes behind it", async () => {
      const streams = installFakeEventSource();
      navigation.go(`${PATH}?sell=YES`);
      const { api } = await open(marketDetail({ viewer: marketViewerWithPosition() }));
      act(() => streams.latest().open());
      // Everything was sold: no sell preset is allowed.
      api.on(MARKET, json(200, marketDetail({ viewer: marketViewer({ cost_cr: "50" }) })));

      act(() =>
        streams.latest().emit("status", statusEvent({ status: "OPEN", trading_open: true })),
      );

      await waitFor(() => expect(button("Sell")).toBeDisabled());
      expect(within(panel()).getByText("Ticket: SELL YES")).toBeInTheDocument();
      expect(navigation.href).toBe(`${PATH}?sell=YES`);
    });

    test("a sell the viewer cannot make leaves the panel on Buy and YES", async () => {
      navigation.go(`${PATH}?sell=YES`);

      await open();

      await waitFor(() => expect(navigation.href).toBe(PATH));
      expect(within(panel()).getByText("Ticket: BUY YES")).toBeInTheDocument();
    });

    test("is in place for a paused market too, where the ticket shows that nothing can be chosen", async () => {
      navigation.go(`${PATH}?buy=NO`);

      await open(halted());

      expect(within(panel()).getByText("Ticket: BUY NO")).toBeInTheDocument();
      expect(within(panel()).getByText("Ticket market: HALTED")).toBeInTheDocument();
      expect(navigation.href).toBe(`${PATH}?buy=NO`);
      expect(button("YES 54.8%")).toBeDisabled();
    });

    test.each(["CLOSED", "RESOLVED", "VOID", "PROPOSED", "REJECTED", "EXPIRED"] as const)(
      "a %s market has no panel, and keeps to the 720 px column",
      async (status) => {
        navigation.go(`${PATH}?buy=YES`);
        const settled = status === "RESOLVED" || status === "VOID";

        await open(
          marketDetail({
            status,
            trading_open: false,
            outcome: status === "RESOLVED" ? "YES" : status === "VOID" ? "VOID" : null,
            resolution: settled
              ? {
                  outcome: status === "RESOLVED" ? "YES" : "VOID",
                  evidence: "Decided.",
                  resolved_at: "2026-10-20T09:30:00Z",
                }
              : null,
            viewer: marketViewer({ can_trade: false, cannot_trade_reason: "MARKET_NOT_OPEN" }),
          }),
        );

        await waitFor(() => expect(navigation.href).toBe(PATH));
        expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
        expect(screen.queryByTestId("ticket")).not.toBeInTheDocument();
        noSheet();
        expect(screen.getByRole("main")).not.toHaveClass("lg:max-w-[67.5rem]");
      },
    );

    test("while the market loads, a block stands where the panel will", () => {
      mockApi({ [MARKET]: deferred().handler });

      renderWithProviders(<MarketDetailScreen marketId={MARKET_ID} />);

      expect(document.querySelector("[data-slot=trade-panel-skeleton]")).toBeInTheDocument();
      expect(screen.queryByTestId("ticket")).not.toBeInTheDocument();
    });

    test("never shows two tickets", async () => {
      navigation.go(`${PATH}?buy=YES`);

      await open();

      expect(screen.getAllByTestId("ticket")).toHaveLength(1);
      expect(ticket()).toHaveAttribute("data-side", "BUY");
    });
  });

  describe("the surface of the ticket has the question as its header", () => {
    const header = () => document.querySelector<HTMLElement>("[data-slot=sheet-header]")!;

    test("in the sheet it stands in the header row, on one line, beside the close cross", async () => {
      const { user } = await open();
      await user.click(button("YES 54.8%"));
      const dialog = await sheet();

      const question = within(header()).getByText(QUESTION);
      expect(question).toHaveClass("truncate", "type-body-sm", "text-muted-foreground");
      expect(dialog).toContainElement(header());
      // The cross is the sheet's own, and the question ends before it.
      expect(within(dialog).getByRole("button", { name: "Close" })).toBeEnabled();
      expect(header()).toHaveClass("pr-12");
    });

    test("the header stands above the ticket and outside of it, so it does not scroll with it", async () => {
      const { user } = await open();
      await user.click(button("YES 54.8%"));
      await sheet();

      expect(ticket()).not.toContainElement(header());
      expect(header().compareDocumentPosition(ticket()) & 4).toBeTruthy();
      expect(header()).toHaveClass("shrink-0");
    });

    test("in the dialog from 768 px it is the same header", async () => {
      setViewport("wide");
      const { user } = await open();
      await user.click(button("YES 54.8%"));
      const dialog = await sheet();

      expect(dialog).toHaveAttribute("data-slot", "dialog-content");
      expect(within(header()).getByText(QUESTION)).toHaveClass("truncate");
      expect(within(dialog).getByRole("button", { name: "Close" })).toBeInTheDocument();
    });

    test("in the panel the question stands alone: a panel is part of the page and has nothing to close", async () => {
      setViewport("desktop");
      await open();

      const panel = screen.getByRole("complementary", { name: "Trade" });
      const question = within(panel).getByText(QUESTION);
      expect(question).toHaveClass("truncate", "type-body-sm", "text-muted-foreground");
      expect(question.compareDocumentPosition(ticket()) & 4).toBeTruthy();
      expect(ticket()).not.toContainElement(question);
      // Above what scrolls: the panel's own first line, which keeps its height.
      expect(question.parentElement).toBe(panel);
      expect(question).toHaveClass("shrink-0");
      expect(within(panel).queryByRole("button", { name: "Close" })).not.toBeInTheDocument();
    });

    test("a long question is cut to its one line there, and is whole on the page", async () => {
      const long =
        "Will the number of students who hand in the first exercise sheet of the semester on time be above the number who hand it in late?";
      mockMarketApi(marketDetail({ question: long }));
      const { user } = renderWithProviders(<MarketDetailScreen marketId={MARKET_ID} />);
      await user.click(await screen.findByRole("button", { name: "YES 54.8%" }));
      await sheet();

      expect(within(header()).getByText(long)).toHaveClass("truncate");
      expect(screen.getByText(long, { selector: "h1" })).not.toHaveClass("truncate");
    });
  });

  describe("while the sheet's trade is on its way", () => {
    const cross = () => screen.getByRole("button", { name: "Close" });

    test("the sheet cannot be closed: the cross stays, disabled, and Escape does nothing", async () => {
      const { user } = await open();
      await user.click(button("YES 54.8%"));
      await sheet();
      expect(cross()).toBeEnabled();

      act(() => sendTrade());

      expect(cross()).toBeDisabled();
      await user.keyboard("{Escape}");
      await new Promise((resolve) => setTimeout(resolve, 30));
      expect(screen.getByRole("dialog", { name: "Trade" })).toBeInTheDocument();
      expect(navigation.href).toBe(`${PATH}?buy=YES`);
    });

    test("once the answer is there it closes as ever", async () => {
      const { user } = await open();
      await user.click(button("YES 54.8%"));
      await sheet();
      act(() => sendTrade());

      act(() => answerTrade());

      expect(cross()).toBeEnabled();
      await user.click(cross());
      await waitFor(noSheet);
      expect(navigation.href).toBe(PATH);
      expect(navigation.depth).toBe(0);
    });

    test("the phone's Back does not take the sheet away: the request goes back into the address", async () => {
      const { user } = await open();
      await user.click(button("NO 45.2%"));
      await sheet();
      act(() => sendTrade());

      act(() => navigation.router.back());

      // The verdict is seen where the trade was made.
      await waitFor(() => expect(navigation.href).toBe(`${PATH}?buy=NO`));
      expect(screen.getByRole("dialog", { name: "Trade" })).toBeInTheDocument();
      expect(screen.getByText("Ticket: BUY NO")).toBeInTheDocument();
      expect(navigation.depth).toBe(1);
      expect(cross()).toBeDisabled();

      // With its answer the sheet is an ordinary sheet again: Back closes it.
      act(() => answerTrade());
      act(() => navigation.router.back());

      await waitFor(noSheet);
      expect(navigation.href).toBe(PATH);
      expect(screen.getByRole("heading", { level: 1, name: QUESTION })).toBeInTheDocument();
    });

    test("a sheet that was kept under Back closes with one step back, as any sheet that was opened by a tap", async () => {
      const { user } = await open();
      await user.click(button("YES 54.8%"));
      await sheet();
      act(() => sendTrade());
      act(() => navigation.router.back());
      await waitFor(() => expect(navigation.href).toBe(`${PATH}?buy=YES`));
      act(() => answerTrade());
      navigation.router.back.mockClear();

      await user.click(cross());

      await waitFor(noSheet);
      expect(navigation.router.back).toHaveBeenCalledTimes(1);
      expect(navigation.href).toBe(PATH);
      expect(navigation.depth).toBe(0);
    });

    test("the tab bar stays hidden while the sheet is kept", async () => {
      mockMarketApi();
      const { user } = renderWithProviders(
        <AppShell>
          <MarketDetailScreen marketId={MARKET_ID} />
        </AppShell>,
      );
      await user.click(await screen.findByRole("button", { name: "YES 54.8%" }));
      await sheet();
      act(() => sendTrade());

      act(() => navigation.router.back());

      await waitFor(() => expect(navigation.href).toBe(`${PATH}?buy=YES`));
      expect(screen.queryByRole("navigation", { name: "Main", hidden: true })).toBeNull();
    });

    test("the same holds for the dialog from 768 px", async () => {
      setViewport("wide");
      const { user } = await open();
      await user.click(button("YES 54.8%"));
      const dialog = await sheet();
      expect(dialog).toHaveAttribute("data-slot", "dialog-content");

      act(() => sendTrade());

      expect(cross()).toBeDisabled();
      await user.keyboard("{Escape}");
      await new Promise((resolve) => setTimeout(resolve, 30));
      expect(screen.getByRole("dialog", { name: "Trade" })).toBeInTheDocument();

      act(() => answerTrade());
      await user.keyboard("{Escape}");
      await waitFor(noSheet);
    });

    test("the screen follows the ticket only while its sheet is open", async () => {
      const { user } = await open();
      // A closed sheet shows no ticket: a trade answered meanwhile must find none on screen.
      expect(followsTicket()).toBe(false);

      await user.click(button("YES 54.8%"));
      await sheet();
      expect(followsTicket()).toBe(true);

      await user.keyboard("{Escape}");
      await waitFor(noSheet);
      expect(followsTicket()).toBe(false);
    });

    test("a trade that is on its way when the market is opened opens no sheet by itself", async () => {
      sendTrade();

      await open();

      await new Promise((resolve) => setTimeout(resolve, 30));
      noSheet();
      expect(navigation.href).toBe(PATH);
      expect(writes.pushState).not.toHaveBeenCalled();
    });

    test("the panel has nothing to lock: from 1024 px the screen does not follow the ticket", async () => {
      setViewport("desktop");
      navigation.go(`${PATH}?buy=NO`);
      await open();

      act(() => sendTrade());

      expect(followsTicket()).toBe(false);
      expect(screen.getByRole("complementary", { name: "Trade" })).toBeInTheDocument();
      noSheet();
      expect(writes.pushState).not.toHaveBeenCalled();
    });
  });

  describe("the outcome buttons on a phone", () => {
    const place = () => document.querySelector("[data-slot=outcome-buttons-place]")!;
    const pinned = () => document.querySelector("[data-slot=pinned]");

    test("stand in their place while it is in view", async () => {
      const scrolling = watchScrolling();
      await open();

      act(() => scrolling.inView(place()));

      expect(
        within(place() as HTMLElement).getByRole("button", { name: "YES 54.8%" }),
      ).toBeInTheDocument();
      expect(pinned()).not.toBeInTheDocument();
    });

    test("are pinned to the bottom of the screen once the page was scrolled past them", async () => {
      const scrolling = watchScrolling();
      await open();

      act(() => scrolling.scrolledPast(place()));

      const bar = pinned() as HTMLElement;
      expect(bar).toBeInTheDocument();
      expect(bar).toHaveClass("sticky", "bottom-(--tab-bar-space)");
      expect(within(bar).getByRole("button", { name: "YES 54.8%" })).toBeEnabled();
      expect(within(bar).getByRole("button", { name: "NO 45.2%" })).toBeEnabled();
      // One pair of buttons, not two: the place keeps its room and is empty.
      expect(screen.getAllByRole("button", { name: "YES 54.8%" })).toHaveLength(1);
      expect(place()).toBeEmptyDOMElement();
      expect(place()).toHaveClass("min-h-12");
    });

    test("go back to their place when it comes into view again", async () => {
      const scrolling = watchScrolling();
      await open();
      act(() => scrolling.scrolledPast(place()));
      expect(pinned()).toBeInTheDocument();

      act(() => scrolling.inView(place()));

      expect(pinned()).not.toBeInTheDocument();
      expect(
        within(place() as HTMLElement).getByRole("button", { name: "NO 45.2%" }),
      ).toBeInTheDocument();
    });

    test("are not pinned before the page has reached them", async () => {
      const scrolling = watchScrolling();
      await open();

      act(() => scrolling.notReached(place()));

      expect(pinned()).not.toBeInTheDocument();
    });

    test("open the sheet from where they are pinned", async () => {
      const scrolling = watchScrolling();
      const { user } = await open();
      act(() => scrolling.scrolledPast(place()));

      await user.click(within(pinned() as HTMLElement).getByRole("button", { name: "NO 45.2%" }));

      expect(navigation.href).toBe(`${PATH}?buy=NO`);
      expect(within(await sheet()).getByText("Ticket: BUY NO")).toBeInTheDocument();
    });

    test("are pinned after a jump that never showed them: a restored scroll position, the End key", async () => {
      const scrolling = watchScrolling();
      await open();
      // A short screen: the buttons lie below it when the market opens.
      act(() => scrolling.notReached(place()));
      expect(pinned()).not.toBeInTheDocument();

      act(() => scrolling.scrolledPast(place()));

      expect(
        within(pinned() as HTMLElement).getByRole("button", { name: "YES 54.8%" }),
      ).toBeEnabled();

      act(() => scrolling.notReached(place()));
      expect(pinned()).not.toBeInTheDocument();
    });

    test("measure against the top bar, which lies over the top of the page, and look below the screen too", async () => {
      const scrolling = watchScrolling();
      await open();

      expect(scrolling.margins()).toHaveLength(1);
      // Down from the lower edge of the top bar, and far past the lower edge of the screen.
      expect(scrolling.margins()[0]).toMatch(/^-\d+px 0px 1000000px 0px$/);
    });

    test("are not pinned from 768 px, where there is no tab bar to stand on", async () => {
      setViewport("wide");
      const scrolling = watchScrolling();
      await open();

      act(() => scrolling.scrolledPast(place()));

      expect(pinned()).not.toBeInTheDocument();
      expect(
        within(place() as HTMLElement).getByRole("button", { name: "YES 54.8%" }),
      ).toBeInTheDocument();
    });

    test("a market without outcome buttons has nothing to pin", async () => {
      watchScrolling();
      await open(
        marketDetail({
          status: "CLOSED",
          trading_open: false,
          viewer: marketViewer({ can_trade: false, cannot_trade_reason: "MARKET_NOT_OPEN" }),
        }),
      );

      expect(document.querySelector("[data-slot=outcome-buttons-place]")).not.toBeInTheDocument();
      expect(pinned()).not.toBeInTheDocument();
    });
  });
});
