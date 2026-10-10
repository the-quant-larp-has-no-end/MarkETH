import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test } from "vitest";

import { AppShell } from "@/components/shell";
import { AppGate } from "@/features/session";
import type { MeResponse } from "@/lib/api/schema";
import { config, me } from "@/test/fixtures";
import { apiError, json, mockApi } from "@/test/http";
import { navigation } from "@/test/navigation";
import { createTestQueryClient } from "@/test/render";

import { WalletScreen } from "./wallet-screen";

const address = () => window.location.pathname + window.location.search;

afterEach(() => {
  window.history.replaceState(null, "", "/");
});

/**
 * The whole way of somebody who scans a code card on their first visit (DESIGN.md §11,
 * §13): the portal signs them in, the profile set-up stands in for the Wallet, the
 * welcome dialog opens over the Wallet, and the redeem sheet waits behind it.
 */
test("a first visit through a code card: set-up, the welcome dialog, then the redeem sheet with the code", async () => {
  window.history.replaceState(null, "", "/wallet?code=4F7KQ2M9XAB3");
  navigation.go("/wallet?code=4F7KQ2M9XAB3");
  let profile: MeResponse | null = null;
  const api = mockApi({
    "GET /api/config": json(200, config()),
    "GET /api/me": () =>
      profile === null
        ? apiError(403, "PROFILE_REQUIRED", "Set up your profile to continue.", {
            name: "Anna Muster",
            email: "anna.muster@ethz.ch",
          })
        : json(200, profile),
    "PUT /api/me/profile": () => {
      profile = me({ username: "anna_m", balance_cr: "500" });
      return json(200, { ...profile, signup_granted: true });
    },
    "GET /api/me/vouchers": json(200, { items: [] }),
    "GET /api/me/ledger": json(200, { items: [], next_cursor: null }),
  });
  const user = userEvent.setup();

  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <AppGate>
        <AppShell>
          <WalletScreen />
        </AppShell>
      </AppGate>
    </QueryClientProvider>,
  );

  // The set-up screen stands in for the Wallet; the address keeps the code meanwhile.
  await user.type(await screen.findByLabelText("Username"), "anna_m");
  await user.selectOptions(screen.getByLabelText("Level"), "BACHELOR");
  await user.selectOptions(screen.getByLabelText("Programme"), "BSc Computer Science");
  expect(address()).toBe("/wallet?code=4F7KQ2M9XAB3");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Create profile" }));

  // The Wallet is there now: the welcome dialog is up, and the redeem sheet waits.
  const welcome = await screen.findByRole("dialog", { name: "You got 500 tokens" });
  expect(screen.getAllByRole("dialog")).toHaveLength(1);
  expect(screen.queryByText("Redeem", { selector: "button" })).not.toBeInTheDocument();
  // The code left the address as soon as the Wallet had read it.
  expect(address()).toBe("/wallet");

  await user.click(within(welcome).getByRole("button", { name: "Start trading" }));

  const sheet = await screen.findByRole("dialog", { name: "Redeem code" });
  expect(within(sheet).getByLabelText("Code")).toHaveValue("4F7K-Q2M9-XAB3");
  await waitFor(() => expect(screen.getAllByRole("dialog")).toHaveLength(1));
  // The code came all this way, and still nothing was sent: the user taps "Redeem".
  expect(api.calls("POST /api/codes/redeem")).toHaveLength(0);
});
