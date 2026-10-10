import { screen, within } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import { EN_DASH, MINUS, TIMES } from "@/lib/format";
import {
  holdings,
  leaderboardEntry,
  ledgerItem,
  MARKET_ID,
  marketSummary,
  portfolioEntry,
  tradeItem,
  voucherItem,
  voucherProduct,
} from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";

import { LeaderboardFigure, LeaderboardRow } from "./leaderboard-row";
import { LedgerRow } from "./ledger-row";
import { RowList } from "./list-row";
import { holdingsText, PositionRow } from "./position-row";
import { ProductRow } from "./product-row";
import { TradeRow } from "./trade-row";
import { VoucherRow } from "./voucher-row";

describe("LedgerRow", () => {
  test("shows the API's label, the time in the configured timezone and the signed amount", () => {
    renderWithProviders(
      <RowList>
        <LedgerRow
          item={ledgerItem({
            description: "Voucher: Coffee",
            amount_cr: "-35",
            created_at: "2026-10-04T10:00:00Z",
          })}
        />
      </RowList>,
    );

    const row = screen.getByRole("button");
    expect(row).toHaveTextContent("Voucher: Coffee");
    expect(row).toHaveTextContent("4 Oct 2026, 12:00");
    expect(row).toHaveTextContent(`${MINUS}35 tokens`);
  });

  test("expands on tap to show the full amount string", async () => {
    const { user } = renderWithProviders(
      <RowList>
        <LedgerRow item={ledgerItem({ amount_cr: "23.874512" })} />
      </RowList>,
    );
    const row = screen.getByRole("button");

    expect(row).toHaveAttribute("aria-expanded", "false");
    expect(row).toHaveTextContent("23.87");
    expect(screen.getByText("Exact amount").parentElement).not.toBeVisible();

    await user.click(row);

    expect(row).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Exact amount").parentElement).toBeVisible();
    expect(screen.getByText("Exact amount").parentElement).toHaveTextContent("23.874512");
  });
});

describe("TradeRow", () => {
  test("names what was done, and links to the market", () => {
    renderWithProviders(
      <RowList>
        <TradeRow
          trade={tradeItem({
            side: "SELL",
            outcome: "NO",
            amount_cr: "12.5",
            shares: "30.123456",
            created_at: "2026-10-04T10:00:00Z",
          })}
        />
      </RowList>,
    );

    const row = screen.getByRole("link");
    expect(row).toHaveAttribute("href", `/markets/${MARKET_ID}`);
    expect(row).toHaveTextContent("Will the Polybahn run without interruption");
    expect(row).toHaveTextContent("Sold NO");
    expect(row).toHaveTextContent("4 Oct 2026, 12:00");
    expect(row).toHaveTextContent("12.5 tokens");
    expect(row).toHaveTextContent("30.12 shares");
  });

  test("says Bought for a buy", () => {
    renderWithProviders(
      <RowList>
        <TradeRow trade={tradeItem({ side: "BUY", outcome: "YES" })} />
      </RowList>,
    );

    expect(screen.getByRole("link")).toHaveTextContent("Bought YES");
  });
});

describe("PositionRow", () => {
  test("an open position names its holdings and shows value_cr", () => {
    renderWithProviders(
      <RowList>
        <PositionRow entry={portfolioEntry({ value_cr: "50.221461" })} />
      </RowList>,
    );

    const row = screen.getByRole("link");
    expect(row).toHaveAttribute("href", `/markets/${MARKET_ID}`);
    expect(row).toHaveTextContent("91.64 YES shares");
    expect(row).toHaveTextContent("50.22 tokens");
    expect(row).not.toHaveTextContent("Paid out");
  });

  test("a holding of no shares is left out, by the string 0 alone", () => {
    expect(holdingsText(holdings({ shares: "91.645" }, { shares: "0" }), "none")).toBe(
      "91.64 YES shares",
    );
    expect(holdingsText(holdings({ shares: "0.000001" }, { shares: "12" }), "none")).toBe(
      "0.00 YES shares, 12 NO shares",
    );
    expect(holdingsText(holdings(), "No shares held")).toBe("No shares held");
  });

  test("a resolved position shows Paid out with payout_cr in place of the value, and its badge", () => {
    renderWithProviders(
      <RowList>
        <PositionRow
          entry={portfolioEntry({
            market: marketSummary({ status: "RESOLVED", outcome: "YES", trading_open: false }),
            value_cr: "91.645",
            payout_cr: "91.645",
          })}
        />
      </RowList>,
    );

    const row = screen.getByRole("link");
    expect(row).toHaveTextContent("Paid out");
    expect(row).toHaveTextContent("91.64 tokens");
    expect(within(row).getByText("Resolved YES")).toBeInTheDocument();
  });

  test("a voided position shows Refunded with refund_cr", () => {
    renderWithProviders(
      <RowList>
        <PositionRow
          entry={portfolioEntry({
            market: marketSummary({ status: "VOID", outcome: "VOID", trading_open: false }),
            holdings: holdings(),
            value_cr: "0",
            refund_cr: "50",
          })}
        />
      </RowList>,
    );

    const row = screen.getByRole("link");
    expect(row).toHaveTextContent("Refunded");
    expect(row).toHaveTextContent("50 tokens");
    expect(row).toHaveTextContent("No shares held");
    expect(within(row).getByText("Voided")).toBeInTheDocument();
  });
});

describe("ProductRow", () => {
  test("shows the name as sent and what it takes, and hands on the product", async () => {
    const onSelect = vi.fn();
    const product = voucherProduct({ name: "M&Ms", price_cr: "15" });
    const { user } = renderWithProviders(
      <RowList>
        <ProductRow product={product} onSelect={onSelect} />
      </RowList>,
    );

    const row = screen.getByRole("button");
    expect(row).toHaveTextContent("M&Ms");
    expect(row).toHaveTextContent("15 tokens");

    await user.click(row);
    expect(onSelect).toHaveBeenCalledWith(product);
  });

  test("is disabled by affordable alone, and stays in the list", () => {
    renderWithProviders(
      <RowList>
        <ProductRow
          product={voucherProduct({ price_cr: "5", affordable: false })}
          onSelect={() => {}}
        />
        <ProductRow
          product={voucherProduct({
            id: "mate",
            name: "Mate",
            price_cr: "999999",
            affordable: true,
          })}
          onSelect={() => {}}
        />
      </RowList>,
    );

    const [cheapButRefused, dearButAllowed] = screen.getAllByRole("button");
    expect(cheapButRefused).toBeDisabled();
    expect(dearButAllowed).toBeEnabled();
  });
});

describe("VoucherRow", () => {
  test("an active voucher shows until when it is valid and a Show QR button", async () => {
    const onShowQr = vi.fn();
    const voucher = voucherItem({ expires_at: "2026-11-03T10:00:00Z" });
    const { user } = renderWithProviders(
      <RowList>
        <VoucherRow voucher={voucher} onShowQr={onShowQr} />
      </RowList>,
    );

    expect(screen.getByText("Coffee")).toBeInTheDocument();
    expect(screen.getByText(/Valid until/)).toHaveTextContent("Valid until 3 Nov 2026, 11:00");

    await user.click(screen.getByRole("button", { name: "Show QR" }));
    expect(onShowQr).toHaveBeenCalledWith(voucher);
  });

  test("a redeemed voucher is muted and says when it was redeemed", () => {
    renderWithProviders(
      <RowList>
        <VoucherRow
          voucher={voucherItem({ status: "REDEEMED", redeemed_at: "2026-10-05T08:02:00Z" })}
          onShowQr={() => {}}
        />
      </RowList>,
    );

    expect(screen.getByText("Redeemed")).toBeInTheDocument();
    expect(screen.getByText("5 Oct 2026, 10:02")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByText("Coffee").closest("div")).toHaveClass("text-muted-foreground");
  });

  test("an expired voucher says that it was refunded", () => {
    renderWithProviders(
      <RowList>
        <VoucherRow voucher={voucherItem({ status: "EXPIRED" })} onShowQr={() => {}} />
      </RowList>,
    );

    expect(screen.getByText("Expired, refunded")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  test("Show QR shows a spinner while its code is on the way", () => {
    renderWithProviders(
      <RowList>
        <VoucherRow voucher={voucherItem()} onShowQr={() => {}} loading />
      </RowList>,
    );

    expect(screen.getByRole("button", { name: "Show QR" })).toBeDisabled();
  });
});

describe("LeaderboardRow", () => {
  test("shows rank, username, programme and the figure of the sorted measure, as sent", () => {
    renderWithProviders(
      <RowList>
        <LeaderboardRow
          entry={leaderboardEntry({ rank: 1204, username: "polyterrasse", credits_cr: "1204.5" })}
          sort="credits"
        />
      </RowList>,
    );

    const row = screen.getByRole("listitem");
    expect(row).toHaveTextContent("Rank 1,204");
    expect(row).toHaveTextContent("polyterrasse");
    expect(row).toHaveTextContent("BSc Computer Science");
    expect(row).toHaveTextContent("1,204.5 tokens");
    expect(row).not.toHaveTextContent("You");
    expect(row).not.toHaveClass("bg-raised");
  });

  test("marks the viewer's own row", () => {
    renderWithProviders(
      <RowList>
        <LeaderboardRow entry={leaderboardEntry({ is_viewer: true })} sort="credits" />
      </RowList>,
    );

    const row = screen.getByRole("listitem");
    expect(row).toHaveClass("bg-raised");
    expect(within(row).getByText("You")).toBeInTheDocument();
  });

  test.each([
    ["win_rate", "62.5%"],
    ["trades", "37"],
    ["avg_payout_ratio", `1.35${TIMES}`],
  ] as const)("sorted by %s it shows %s", (sort, figure) => {
    renderWithProviders(
      <RowList>
        <LeaderboardRow entry={leaderboardEntry()} sort={sort} />
      </RowList>,
    );

    expect(screen.getByText(figure)).toBeInTheDocument();
  });

  test("a missing rate is an en dash on screen and none yet for a screen reader", () => {
    renderWithProviders(
      <LeaderboardFigure
        entry={leaderboardEntry({ win_rate: null, avg_payout_ratio: null })}
        sort="win_rate"
      />,
    );

    expect(screen.getByText(EN_DASH)).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByText("none yet")).toHaveClass("sr-only");
  });

  test("carries no trophy unless it is asked to, and then only for the first three ranks", () => {
    const { rerender } = renderWithProviders(
      <RowList>
        <LeaderboardRow entry={leaderboardEntry({ rank: 2 })} sort="credits" />
      </RowList>,
    );
    expect(screen.queryByRole("img", { name: /place/ })).not.toBeInTheDocument();

    rerender(
      <RowList>
        <LeaderboardRow entry={leaderboardEntry({ rank: 2 })} sort="credits" trophy />
      </RowList>,
    );
    expect(screen.getByRole("img", { name: "Second place" })).toBeInTheDocument();

    rerender(
      <RowList>
        <LeaderboardRow entry={leaderboardEntry({ rank: 4 })} sort="credits" trophy />
      </RowList>,
    );
    expect(screen.queryByRole("img", { name: /place/ })).not.toBeInTheDocument();
  });
});
