import type { MeResponse } from "@/lib/api/schema";
import { me as meFixture } from "@/test/fixtures";
import { type Handler, json, mockApi, type Route } from "@/test/http";
import { renderWithProviders } from "@/test/render";

import { WalletScreen } from "./wallet-screen";

/**
 * What the tests of the Wallet share. It is imported by `*.test.tsx` files only.
 */

/** The operations the Wallet uses, by a short name. */
export const WALLET = {
  me: "GET /api/me",
  vouchers: "GET /api/me/vouchers",
  ledger: "GET /api/me/ledger",
  products: "GET /api/vouchers/products",
  issue: "POST /api/vouchers",
  code: "POST /api/vouchers/{voucher_id}/code",
  redeem: "POST /api/codes/redeem",
} as const satisfies Record<string, Route>;

/**
 * Renders the Wallet for `profile`, with an API that answers its three reads: the
 * profile, no vouchers and no activity. `routes` adds to that or replaces it.
 */
export function renderWallet(
  routes: Partial<Record<Route, Handler>> = {},
  profile: MeResponse = meFixture(),
) {
  const api = mockApi({
    [WALLET.me]: json(200, profile),
    [WALLET.vouchers]: json(200, { items: [] }),
    [WALLET.ledger]: json(200, { items: [], next_cursor: null }),
    ...routes,
  });
  return { api, ...renderWithProviders(<WalletScreen />, { me: profile }) };
}

/** The balance card, once it shows the balance. */
export function balanceCard(): HTMLElement {
  const card = document.querySelector<HTMLElement>("[data-slot=balance-card]");
  if (card === null) throw new Error("The balance card is not on screen.");
  return card;
}
