import { screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { sizeCharts } from "@/test/charts";
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
import { deferred, json, mockApi, noAnswer, type Route } from "@/test/http";
import { setViewport } from "@/test/media";
import { followHistoryWrites, navigation } from "@/test/navigation";
import { renderWithProviders } from "@/test/render";

import { MarketDetailScreen } from "./index";

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

describe("from 1024 px", () => {
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
        }),
      }),
    );

    const panel = await screen.findByRole("complementary", { name: "Trade" });
    for (const amount of ["25", "50", "100"]) {
      expect(within(panel).getByRole("button", { name: `${amount} tokens` })).toBeDisabled();
    }
    expect(within(panel).getByRole("button", { name: "Buy YES" })).toBeDisabled();
  });
});
