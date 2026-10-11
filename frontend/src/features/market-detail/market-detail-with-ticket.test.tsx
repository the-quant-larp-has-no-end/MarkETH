import { act, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { sizeCharts } from "@/test/charts";
import { installFakeEventSource } from "@/test/fake-event-source";
import {
  history,
  MARKET_ID,
  marketDetail,
  marketViewer,
  marketViewerWithPosition,
  me,
  quote,
  tradeResponse,
} from "@/test/fixtures";
import { apiError, deferred, json, mockApi, noAnswer, type Route } from "@/test/http";
import { setViewport } from "@/test/media";
import { followHistoryWrites, navigation } from "@/test/navigation";
import { renderWithProviders } from "@/test/render";

import { MarketDetailScreen } from "./index";

vi.mock("@/features/broadcast", async () => (await import("./testing")).broadcastFeature);

/**
 * The trade ticket of `@/features/trade` inside this screen: neither side is a stand-in.
 * The other tests of the two features each put a stand-in in place of the other; this is
 * the check that they fit together, from the tap on an outcome to the receipt.
 */

const MARKET: Route = "GET /api/markets/{market_id}";
const HISTORY: Route = "GET /api/markets/{market_id}/history";
const QUOTE: Route = "GET /api/markets/{market_id}/quote";
const TRADE: Route = "POST /api/markets/{market_id}/trades";
const ME: Route = "GET /api/me";
const PATH = `/markets/${MARKET_ID}`;

const QUESTION = "Will the Polybahn run without interruption during the exam session?";

const ticket = () => document.querySelector<HTMLElement>("[data-slot=trade-ticket]");
const sheet = () => screen.findByRole("dialog", { name: "Trade" });

function open(market = marketDetail(), address = PATH) {
  navigation.go(address);
  const api = mockApi({
    [MARKET]: json(200, market),
    [HISTORY]: json(200, history()),
    [QUOTE]: json(200, quote()),
    [TRADE]: json(201, tradeResponse()),
    // The ticket reads the balance again when it opens, and so does a trade.
    [ME]: json(200, me()),
  });
  const view = renderWithProviders(<MarketDetailScreen marketId={MARKET_ID} />);
  return { ...view, api };
}

beforeEach(() => {
  followHistoryWrites();
  sizeCharts();
  // Three days before the market of the fixtures closes.
  vi.useFakeTimers({ now: new Date("2026-10-11T10:00:00Z"), toFake: ["Date"] });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("on a phone", () => {
  test("an outcome button opens the sheet with the ticket, which reads the market again", async () => {
    const { user, api } = open();
    await user.click(await screen.findByRole("button", { name: "YES 54.8%" }));

    const dialog = await sheet();
    expect(dialog).toHaveAttribute("data-slot", "drawer-content");
    expect(dialog).toContainElement(ticket());
    expect(ticket()).toHaveAttribute("data-side", "BUY");
    expect(ticket()).toHaveAttribute("data-outcome", "YES");
    expect(navigation.href).toBe(`${PATH}?buy=YES`);
    await waitFor(() => expect(api.calls(MARKET)).toHaveLength(2));
    // The ticket's own body and footer are the sheet's: the button is pinned in the footer.
    expect(dialog.querySelector("[data-slot=sheet-footer]")).toContainElement(
      within(dialog).getByRole("button", { name: "Buy YES" }),
    );
  });

  test("the sheet's header is the question beside the close cross, above what scrolls; the ticket does not say it again", async () => {
    const { user } = open();
    await user.click(await screen.findByRole("button", { name: "YES 54.8%" }));
    const dialog = await sheet();

    const header = dialog.querySelector<HTMLElement>("[data-slot=sheet-header]")!;
    expect(within(header).getByText(QUESTION)).toHaveClass("truncate", "type-body-sm");
    expect(within(dialog).getAllByText(QUESTION)).toHaveLength(1);
    expect(ticket()).not.toContainElement(header);
    const body = dialog.querySelector("[data-slot=sheet-body]")!;
    expect(body).not.toContainElement(header);
    expect(header.compareDocumentPosition(body) & 4).toBeTruthy();
    expect(within(dialog).getByRole("button", { name: "Close" })).toBeEnabled();
  });

  test("a typed amount from the first keystroke to the receipt", async () => {
    const { user, api } = open();
    api.on(QUOTE, json(200, quote({ amount_cr: "37.5", shares: "67.4" })));
    await user.click(await screen.findByRole("button", { name: "NO 45.2%" }));
    const dialog = await sheet();
    const field = within(dialog).getByRole("textbox", { name: "Stake in tokens" });
    expect(field).toHaveValue("");

    await user.type(field, "37,50");

    await waitFor(() =>
      expect(within(dialog).getByRole("button", { name: "Buy NO" })).toBeEnabled(),
    );
    expect(api.lastCall(QUOTE)?.query.get("amount_cr")).toBe("37.50");
    await user.click(within(dialog).getByRole("button", { name: "Buy NO" }));

    await within(dialog).findByRole("heading", { name: /^Bought/ });
    expect(api.lastCall(TRADE)?.rawBody).toBe(
      '{"side":"BUY","outcome":"NO","amount_cr":"37.50","min_shares":"67.4","quote_version":7}',
    );
  });

  test("an amount the server refuses is said with the stake entry, and no trade can be sent for it", async () => {
    const { user, api } = open();
    api.on(
      QUOTE,
      apiError(409, "LIMIT_EXCEEDED", "This would exceed a limit.", {
        limit: "MAX_STAKE_PER_USER_MARKET",
        stake_limit_cr: "100",
        stake_left_cr: "100",
      }),
    );
    await user.click(await screen.findByRole("button", { name: "YES 54.8%" }));
    const dialog = await sheet();

    await user.type(within(dialog).getByRole("textbox", { name: "Stake in tokens" }), "250");

    const refusal = await within(dialog).findByRole("alert");
    expect(refusal).toHaveTextContent("This would exceed a limit.");
    expect(dialog.querySelector("[data-slot=stake-entry]")).toContainElement(refusal);
    expect(within(dialog).getByRole("button", { name: "Buy YES" })).toBeDisabled();
    expect(api.calls(TRADE)).toHaveLength(0);
  });

  test("the sheet cannot be closed while its trade is on its way: not by its cross, not by Escape, not by the phone's Back", async () => {
    const { user, api } = open();
    const answer = deferred();
    api.on(TRADE, answer.handler);
    await user.click(await screen.findByRole("button", { name: "YES 54.8%" }));
    const dialog = await sheet();
    await user.click(within(dialog).getByRole("button", { name: "25 tokens" }));
    await waitFor(() =>
      expect(within(dialog).getByRole("button", { name: "Buy YES" })).toBeEnabled(),
    );

    await user.click(within(dialog).getByRole("button", { name: "Buy YES" }));
    await waitFor(() => expect(api.calls(TRADE)).toHaveLength(1));

    const cross = () => screen.getByRole("button", { name: "Close" });
    await waitFor(() => expect(cross()).toBeDisabled());
    await user.keyboard("{Escape}");
    act(() => navigation.router.back());
    await waitFor(() => expect(navigation.href).toBe(`${PATH}?buy=YES`));
    expect(screen.getByRole("dialog", { name: "Trade" })).toBeInTheDocument();
    expect(ticket()).toHaveAttribute("data-view", "form");

    // The verdict is seen where the trade was made, and the receipt keeps the cross.
    answer.resolve(json(201, tradeResponse()));
    expect(
      await screen.findByRole("heading", { name: "Bought 45.123456 YES shares" }),
    ).toBeInTheDocument();
    await waitFor(() => expect(cross()).toBeEnabled());
    expect(api.calls(TRADE)).toHaveLength(1);

    await user.click(cross());

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(navigation.href).toBe(PATH);
    // The receipt was read and put away: the next opening is an empty form.
    await new Promise((resolve) => setTimeout(resolve, 30));
    await user.click(await screen.findByRole("button", { name: /^YES / }));
    await sheet();
    expect(ticket()).toHaveAttribute("data-view", "form");
    expect(screen.getByRole("textbox", { name: "Stake in tokens" })).toHaveValue("");
  });

  test("a refusal is read in the sheet that sent the trade, which can then be closed", async () => {
    const { user, api } = open();
    const answer = deferred();
    api.on(TRADE, answer.handler);
    await user.click(await screen.findByRole("button", { name: "YES 54.8%" }));
    const dialog = await sheet();
    await user.click(within(dialog).getByRole("button", { name: "25 tokens" }));
    await waitFor(() =>
      expect(within(dialog).getByRole("button", { name: "Buy YES" })).toBeEnabled(),
    );
    await user.click(within(dialog).getByRole("button", { name: "Buy YES" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Close" })).toBeDisabled());

    answer.resolve(apiError(422, "TRADE_TOO_SMALL", "This trade is below the minimum size."));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "This trade is below the minimum size.",
    );
    await waitFor(() => expect(screen.getByRole("button", { name: "Close" })).toBeEnabled());
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  test("a buy from the first tap to Done: the receipt, then the sheet closes and the address is clean", async () => {
    const { user, api } = open();
    await user.click(await screen.findByRole("button", { name: "YES 54.8%" }));
    const dialog = await sheet();

    await user.click(within(dialog).getByRole("button", { name: "25 tokens" }));
    await waitFor(() =>
      expect(within(dialog).getByRole("button", { name: "Buy YES" })).toBeEnabled(),
    );
    await user.click(within(dialog).getByRole("button", { name: "Buy YES" }));

    expect(
      await within(dialog).findByRole("heading", { name: "Bought 45.123456 YES shares" }),
    ).toHaveFocus();
    expect(api.lastCall(TRADE)?.rawBody).toBe(
      '{"side":"BUY","outcome":"YES","amount_cr":"25","min_shares":"45.12","quote_version":7}',
    );

    await user.click(within(dialog).getByRole("button", { name: "Done" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(navigation.href).toBe(PATH);

    // The next opening is an empty form.
    await new Promise((resolve) => setTimeout(resolve, 30));
    await user.click(await screen.findByRole("button", { name: /^NO / }));
    const again = await sheet();
    expect(ticket()).toHaveAttribute("data-view", "form");
    expect(ticket()).toHaveAttribute("data-outcome", "NO");
    expect(within(again).getByRole("button", { name: "25 tokens" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  test("a trade moves the balance in the top bar: from the trade's answer at once, then from the API", async () => {
    const { user, api } = open();
    expect(await screen.findByRole("link", { name: "Wallet, 240 tokens" })).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "YES 54.8%" }));
    const dialog = await sheet();
    await user.click(within(dialog).getByRole("button", { name: "25 tokens" }));
    await waitFor(() =>
      expect(within(dialog).getByRole("button", { name: "Buy YES" })).toBeEnabled(),
    );
    const opened = api.calls(ME).length;
    const slow = deferred();
    api.on(ME, slow.handler);

    await user.click(within(dialog).getByRole("button", { name: "Buy YES" }));
    await within(dialog).findByRole("heading", { name: /^Bought/ });

    // The page behind the sheet is hidden from a screen reader while the sheet is open.
    const pill = (balance: string) =>
      screen.queryByRole("link", { name: `Wallet, ${balance} tokens`, hidden: true });
    expect(pill("215")).toBeInTheDocument();
    await waitFor(() => expect(api.calls(ME).length).toBe(opened + 1));

    slow.resolve(json(200, me({ balance_cr: "215.5" })));
    await waitFor(() => expect(pill("215.5")).toBeInTheDocument());
  });

  test("opening the sheet reads the balance again, so the top bar and the sheet agree", async () => {
    const { user, api } = open(marketDetail({ viewer: marketViewer({ balance_cr: "440" }) }));
    await screen.findByRole("button", { name: "YES 54.8%" });
    expect(api.calls(ME)).toHaveLength(0);
    api.on(ME, json(200, me({ balance_cr: "440" })));

    await user.click(screen.getByRole("button", { name: "YES 54.8%" }));
    const dialog = await sheet();

    expect(within(dialog).getByText("Balance").parentElement).toHaveTextContent("440 tokens");
    await waitFor(() =>
      expect(
        screen.getByRole("link", { name: "Wallet, 440 tokens", hidden: true }),
      ).toBeInTheDocument(),
    );
    expect(api.calls(ME)).toHaveLength(1);
  });

  test("the outcome and the side chosen in the ticket go into the address", async () => {
    const { user } = open(marketDetail({ viewer: marketViewerWithPosition() }));
    await user.click(await screen.findByRole("button", { name: "YES 54.8%" }));
    const dialog = await sheet();

    await user.click(within(dialog).getByRole("button", { name: "NO" }));
    await waitFor(() => expect(navigation.href).toBe(`${PATH}?buy=NO`));
    expect(ticket()).toHaveAttribute("data-outcome", "NO");

    await user.click(within(dialog).getByRole("radio", { name: "Sell" }));
    await waitFor(() => expect(navigation.href).toBe(`${PATH}?sell=YES`));
    expect(within(dialog).getByRole("button", { name: "Sell YES shares" })).toBeInTheDocument();
  });

  test("an address that asks for a sale opens the ticket on it", async () => {
    open(marketDetail({ viewer: marketViewerWithPosition() }), `${PATH}?sell=YES`);

    const dialog = await sheet();
    expect(within(dialog).getByRole("radio", { name: "Sell" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(within(dialog).getByRole("button", { name: /^Half/ })).toHaveTextContent("45.82 shares");
  });

  test("an unanswered trade is there again when the sheet was closed and opened on something else", async () => {
    const { user, api } = open();
    api.on(TRADE, noAnswer);
    await user.click(await screen.findByRole("button", { name: "YES 54.8%" }));
    let dialog = await sheet();
    await user.click(within(dialog).getByRole("button", { name: "25 tokens" }));
    await waitFor(() =>
      expect(within(dialog).getByRole("button", { name: "Buy YES" })).toBeEnabled(),
    );
    await user.click(within(dialog).getByRole("button", { name: "Buy YES" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("No connection. Try again.");

    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await new Promise((resolve) => setTimeout(resolve, 30));
    await user.click(await screen.findByRole("button", { name: /^NO / }));

    dialog = await sheet();
    expect(ticket()).toHaveAttribute("data-outcome", "YES");
    expect(within(dialog).getByRole("alert")).toHaveTextContent("No connection. Try again.");
    const sent = api.calls(TRADE);
    expect(sent).toHaveLength(1);

    api.on(TRADE, json(201, tradeResponse()));
    await user.click(within(dialog).getByRole("button", { name: "Try again" }));
    await within(dialog).findByRole("heading", { name: /^Bought/ });
    const [first, second] = api.calls(TRADE);
    expect(second?.rawBody).toBe(first?.rawBody);
    expect(second?.headers.get("Idempotency-Key")).toBe(first?.headers.get("Idempotency-Key"));
  });
});

describe("when the close time passes while the sheet is open", () => {
  test("the market is asked again, and the ticket shows that nothing can be traded", async () => {
    // Twenty seconds before the market of the fixtures closes.
    vi.useRealTimers();
    vi.useFakeTimers({ now: new Date("2026-10-14T15:59:40Z"), shouldAdvanceTime: true });
    // The stream is open, so nothing but the close time makes the screen ask.
    const streams = installFakeEventSource();
    const { user, api } = open();
    await user.click(await screen.findByRole("button", { name: "YES 54.8%" }));
    act(() => streams.latest().open());
    const dialog = await sheet();
    await user.click(within(dialog).getByRole("button", { name: "25 tokens" }));
    await waitFor(() =>
      expect(within(dialog).getByRole("button", { name: "Buy YES" })).toBeEnabled(),
    );
    const asked = api.calls(MARKET).length;
    api.on(
      MARKET,
      json(
        200,
        marketDetail({
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
        }),
      ),
    );

    await act(() => vi.advanceTimersByTimeAsync(20_500));

    await waitFor(() => expect(api.calls(MARKET)).toHaveLength(asked + 1));
    // The sheet stays, so that the user sees what became of what they were doing.
    await waitFor(() =>
      expect(within(dialog).getByRole("textbox", { name: "Stake in tokens" })).toBeDisabled(),
    );
    expect(within(dialog).getByRole("button", { name: "Buy YES" })).toBeDisabled();
    expect(within(dialog).queryByRole("slider")).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Trade" })).toBeInTheDocument();
    expect(api.calls(TRADE)).toHaveLength(0);
  });
});

describe("from 1024 px", () => {
  test("the panel has the question as its first line, above the ticket, and no cross", async () => {
    setViewport("desktop");
    open();

    const panel = await screen.findByRole("complementary", { name: "Trade" });
    const question = within(panel).getByText(QUESTION);
    expect(question).toHaveClass("truncate", "type-body-sm", "text-muted-foreground", "shrink-0");
    expect(question.compareDocumentPosition(ticket()!) & 4).toBeTruthy();
    expect(within(panel).getAllByText(QUESTION)).toHaveLength(1);
    expect(panel.querySelector("[data-slot=sheet-body]")).not.toContainElement(question);
    expect(within(panel).queryByRole("button", { name: "Close" })).not.toBeInTheDocument();
  });

  test("the panel has the stake entry: the slider, the amount field and the presets", async () => {
    setViewport("desktop");
    const { user, api } = open();
    const panel = await screen.findByRole("complementary", { name: "Trade" });

    const stake = within(panel).getByRole("group", { name: "Stake" });
    expect(
      within(stake).getByRole("slider", { name: "Stake in whole tokens" }),
    ).toBeInTheDocument();
    await user.type(within(stake).getByRole("textbox", { name: "Stake in tokens" }), "40");

    await waitFor(() => expect(api.lastCall(QUOTE)?.query.get("amount_cr")).toBe("40"));
    expect(within(stake).getByRole("slider")).toHaveAttribute("aria-valuenow", "40");
  });

  test("the ticket stands in the panel, and Done brings the form back in place", async () => {
    setViewport("desktop");
    const { user } = open();

    const panel = await screen.findByRole("complementary", { name: "Trade" });
    expect(panel).toContainElement(ticket());
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await user.click(within(panel).getByRole("button", { name: "25 tokens" }));
    await waitFor(() =>
      expect(within(panel).getByRole("button", { name: "Buy YES" })).toBeEnabled(),
    );
    await user.click(within(panel).getByRole("button", { name: "Buy YES" }));
    await within(panel).findByRole("heading", { name: /^Bought/ });

    await user.click(within(panel).getByRole("button", { name: "Done" }));

    expect(ticket()).toHaveAttribute("data-view", "form");
    expect(ticket()).toHaveFocus();
    expect(within(panel).getByRole("button", { name: "Buy YES" })).toBeDisabled();
  });

  test("a market the viewer cannot trade shows the ticket with nothing to choose", async () => {
    setViewport("desktop");
    open(
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
      }),
    );

    const panel = await screen.findByRole("complementary", { name: "Trade" });
    for (const amount of ["25", "50", "100"]) {
      expect(within(panel).getByRole("button", { name: `${amount} tokens` })).toBeDisabled();
    }
    expect(within(panel).getByRole("textbox", { name: "Stake in tokens" })).toBeDisabled();
    expect(within(panel).queryByRole("slider")).not.toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Buy YES" })).toBeDisabled();
  });
});
