import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import { keys } from "@/lib/api/keys";
import { MINUS } from "@/lib/format";
import { ledgerItem, me, voucherItem } from "@/test/fixtures";
import { apiError, deferred, json, mockApi } from "@/test/http";
import { setViewport } from "@/test/media";
import { navigation } from "@/test/navigation";
import { intersect } from "@/test/observers";
import { createTestQueryClient, renderWithProviders } from "@/test/render";

import { balanceCard, renderWallet, WALLET } from "./testing";
import { WalletScreen } from "./wallet-screen";

const UNAVAILABLE = apiError(
  503,
  "SERVICE_UNAVAILABLE",
  "This is temporarily unavailable. Try again shortly.",
);

const skeletons = () => document.querySelectorAll("[data-slot=row-skeletons]");

describe("while the Wallet loads", () => {
  test("each part is a skeleton of itself: the balance card, two voucher rows, five ledger rows", async () => {
    const profile = deferred();
    const vouchers = deferred();
    const ledger = deferred();
    renderWallet({
      [WALLET.me]: profile.handler,
      [WALLET.vouchers]: vouchers.handler,
      [WALLET.ledger]: ledger.handler,
    });

    expect(document.querySelector("[data-slot=balance-card-skeleton]")).toBeInTheDocument();
    expect(document.querySelector("[data-slot=balance-card]")).not.toBeInTheDocument();
    expect(skeletons()).toHaveLength(2);
    expect(skeletons()[0]!.children).toHaveLength(2);
    expect(skeletons()[1]!.children).toHaveLength(5);
    // The two actions and the titles of the sections do not wait.
    expect(screen.getByRole("button", { name: "Get a voucher" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Redeem code" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Vouchers" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Activity" })).toBeInTheDocument();

    profile.resolve(json(200, me({ balance_cr: "440" })));
    expect(await within(await waitFor(balanceCard)).findByText("440 tokens")).toBeInTheDocument();
    expect(skeletons()).toHaveLength(2);

    vouchers.resolve(json(200, { items: [] }));
    ledger.resolve(json(200, { items: [], next_cursor: null }));
    expect(await screen.findByText("No vouchers yet.")).toBeInTheDocument();
    await waitFor(() => expect(skeletons()).toHaveLength(0));
  });

  test("on a return visit what was read before is on screen at once, and is replaced by the answers", async () => {
    const client = createTestQueryClient();
    client.setQueryData(keys.vouchers, { items: [voucherItem({ product_name: "Mate" })] });
    client.setQueryData(keys.ledger, {
      pages: [{ items: [ledgerItem({ description: "Welcome tokens" })], next_cursor: null }],
      pageParams: [null],
    });
    const profile = deferred();
    mockApi({
      [WALLET.me]: profile.handler,
      [WALLET.vouchers]: json(200, { items: [voucherItem({ product_name: "Mate" })] }),
      [WALLET.ledger]: json(200, { items: [ledgerItem()], next_cursor: null }),
    });

    renderWithProviders(<WalletScreen />, { client, me: me({ balance_cr: "240" }) });

    expect(within(balanceCard()).getByText("240 tokens")).toBeInTheDocument();
    expect(screen.getByText("Mate")).toBeInTheDocument();
    expect(screen.getByText("Welcome tokens")).toBeInTheDocument();
    expect(skeletons()).toHaveLength(0);

    // Monthly tokens were booked since the last visit: they simply show.
    profile.resolve(json(200, me({ balance_cr: "440" })));
    expect(await within(balanceCard()).findByText("440 tokens")).toBeInTheDocument();
    expect(await screen.findByText("Monthly tokens")).toBeInTheDocument();
  });
});

describe("the Wallet with content", () => {
  test("shows the balance card: the balance as sent, its label and the next monthly tokens", async () => {
    renderWallet(
      {},
      me({
        balance_cr: "1250.5",
        monthly_grant_cr: "200",
        next_monthly_grant_at: "2026-11-21T07:15:00Z",
      }),
    );

    const card = within(await waitFor(balanceCard));
    expect(card.getByText("Your tokens")).toBeInTheDocument();
    expect(card.getByText("1,250.5 tokens")).toHaveClass("sr-only");
    const figure = balanceCard().querySelector("[data-slot=amount]");
    expect(figure).toHaveClass("type-display");
    expect(figure).toHaveTextContent("1,250.5");
    // An instant, in the timezone of /config: the day and the time of day.
    expect(card.getByText(/Next monthly tokens:/)).toHaveTextContent(
      "Next monthly tokens: 200 on 21 Nov 2026, 08:15",
    );
    // The token icon beside the balance is the 32 px one.
    expect(balanceCard().querySelector("[data-slot=token-icon]")).toHaveClass("size-8");
  });

  test("reads the balance again when it opens, and asks no list for a page size", async () => {
    const { api } = renderWallet();

    await screen.findByText("No vouchers yet.");
    await screen.findByText("Nothing here yet.");

    expect(api.calls(WALLET.me)).toHaveLength(1);
    expect(api.calls(WALLET.vouchers)).toHaveLength(1);
    expect(api.calls(WALLET.ledger)).toHaveLength(1);
    expect(api.lastCall(WALLET.ledger)?.query.toString()).toBe("");
    for (const request of api.requests) {
      expect(request.headers.get("Accept")).toBe("application/json");
      expect(new URL(request.url).origin).toBe(window.location.origin);
    }
  });

  test("an amount stays the string the API sent: this balance fits no float", async () => {
    const { user } = renderWallet(
      {
        [WALLET.ledger]: json(200, {
          items: [ledgerItem({ id: 1, description: "Code redeemed", amount_cr: "0.000001" })],
          next_cursor: null,
        }),
      },
      me({ balance_cr: "999999999999.999999", monthly_grant_cr: "200.999999" }),
    );

    // Cut after the second decimal, never rounded up to the next whole.
    const card = within(await waitFor(balanceCard));
    expect(card.getByText("999,999,999,999.99 tokens")).toBeInTheDocument();
    expect(card.getByText(/Next monthly tokens:/)).toHaveTextContent(
      "Next monthly tokens: 200.99 on 21 Oct 2026, 10:15",
    );

    // The expanded ledger row shows every digit that arrived.
    await user.click(await screen.findByRole("button", { name: /Code redeemed/ }));
    expect(screen.getByText("Exact amount").parentElement).toHaveTextContent("0.000001");
  });

  test("no amount is ever handed to a number parser", async () => {
    // Every function of the language that makes a number out of text, wherever it lives.
    const names = Object.getOwnPropertyNames(Number).filter((name) => name.startsWith("parse"));
    expect(names).toHaveLength(2);
    const parsers = (names as "parseInt"[]).flatMap((name) => [
      vi.spyOn(globalThis, name),
      vi.spyOn(Number, name),
    ]);
    const amounts = ["1234.567891", "987.654321", "-35.000001", "12.874512", "35.5"];
    const { user } = renderWallet(
      {
        [WALLET.vouchers]: json(200, { items: [voucherItem({ value_cr: amounts[4] })] }),
        [WALLET.ledger]: json(200, {
          items: [
            ledgerItem({ id: 2, description: "Voucher: Coffee", amount_cr: amounts[2] }),
            ledgerItem({ id: 1, description: "Code redeemed", amount_cr: amounts[3] }),
          ],
          next_cursor: null,
        }),
      },
      me({ balance_cr: amounts[0], monthly_grant_cr: amounts[1] }),
    );

    // Everything that shows an amount is on screen, the exact figure of a row included.
    expect(await within(await waitFor(balanceCard)).findByText("1,234.56 tokens")).toBeVisible();
    await user.click(await screen.findByRole("button", { name: /Code redeemed/ }));
    expect(screen.getAllByText("Exact amount")[1]!.parentElement).toHaveTextContent("12.874512");

    const parsed = parsers.flatMap((parser) => parser.mock.calls.map(([text]) => String(text)));
    for (const amount of amounts) {
      expect(parsed).not.toContain(amount);
      expect(parsed).not.toContain(amount.replace("-", ""));
    }
  });

  test("lists the vouchers in the API's order, each with what it offers", async () => {
    renderWallet({
      [WALLET.vouchers]: json(200, {
        items: [
          voucherItem({
            id: "a",
            product_name: "Mate",
            status: "ACTIVE",
            expires_at: "2026-11-03T10:00:00Z",
          }),
          voucherItem({
            id: "b",
            product_name: "Coffee",
            status: "REDEEMED",
            redeemed_at: "2026-10-05T08:02:00Z",
          }),
          voucherItem({
            id: "c",
            product_name: "Twix",
            status: "EXPIRED",
            refunded: true,
            expires_at: "2026-09-30T10:00:00Z",
          }),
          voucherItem({
            id: "d",
            product_name: "Tea",
            status: "EXPIRED",
            refunded: false,
            expires_at: "2026-10-09T10:00:00Z",
          }),
        ],
      }),
    });

    const rows = within(
      (await screen.findByRole("heading", { name: "Vouchers" })).closest("section")!,
    );
    await rows.findByText("Mate");
    const items = rows.getAllByRole("listitem");
    expect(items.map((item) => item.querySelector(".type-body")?.textContent)).toEqual([
      "Mate",
      "Coffee",
      "Twix",
      "Tea",
    ]);

    // Times are shown in the timezone of /config, not the device's.
    expect(items[0]).toHaveTextContent("Valid until 3 Nov 2026, 11:00");
    expect(within(items[0]!).getByRole("button", { name: "Show QR" })).toBeEnabled();
    expect(items[1]).toHaveTextContent("Redeemed");
    expect(items[1]).toHaveTextContent("5 Oct 2026, 10:02");
    // An expired voucher says whether its tokens are back, which is `refunded` of its row.
    expect(items[2]!.textContent).toBe("Twix30 Sep 2026, 12:00Expired, refunded");
    expect(items[3]!.textContent).toBe("Tea9 Oct 2026, 12:00Expired, refund on its way");
    expect(rows.getAllByRole("button", { name: "Show QR" })).toHaveLength(1);
  });

  test("whether a refund is booked is read from `refunded` alone, whatever the balance says", async () => {
    // The balance already holds the tokens, and the row still says what the API says of it.
    renderWallet(
      {
        [WALLET.vouchers]: json(200, {
          items: [voucherItem({ status: "EXPIRED", refunded: false, value_cr: "35" })],
        }),
        [WALLET.ledger]: json(200, {
          items: [
            ledgerItem({ description: "Voucher expired, refunded: Coffee", amount_cr: "35" }),
          ],
          next_cursor: null,
        }),
      },
      me({ balance_cr: "275" }),
    );

    const row = (await screen.findByText("Coffee")).closest("li")!;
    expect(row).toHaveTextContent("Expired, refund on its way");
    expect(row).not.toHaveTextContent("Expired, refunded");
  });

  test("ends with a footer whose link leads to the usage policy", async () => {
    const { user } = renderWallet();
    await screen.findByText("Nothing here yet.");

    const footer = document.querySelector<HTMLElement>("[data-slot=wallet-footer]")!;
    expect(footer).toHaveClass("type-body-sm", "text-muted-foreground");
    // At the foot of the screen: after the activity, the last thing in it.
    expect(screen.getByRole("main").lastElementChild).toBe(footer);
    const link = within(footer).getByRole("link", { name: "Usage policy" });
    expect(link).toHaveAttribute("href", "/policy");
    // It is a text link in its own right: white, underlined, with a target 44 px tall.
    expect(link).toHaveAttribute("data-slot", "text-link");
    expect(link).toHaveClass("min-h-11", "underline", "text-foreground");
    // The page has no form to keep, so the policy opens in place.
    expect(link).not.toHaveAttribute("target");

    await user.click(link);
    expect(navigation.pathname).toBe("/policy");
  });

  test("the footer stands also while the Wallet loads and when it could not be read", async () => {
    renderWallet({ [WALLET.vouchers]: UNAVAILABLE, [WALLET.ledger]: UNAVAILABLE });

    expect(screen.getByRole("link", { name: "Usage policy" })).toBeInTheDocument();
    await screen.findByRole("alert");
    expect(screen.getByRole("link", { name: "Usage policy" })).toBeInTheDocument();
  });

  test("lists the activity as the ledger sends it: its label, its time and its signed amount", async () => {
    renderWallet({
      [WALLET.ledger]: json(200, {
        items: [
          ledgerItem({
            id: 3,
            description: "Voucher: Coffee",
            amount_cr: "-35",
            created_at: "2026-10-04T10:00:00Z",
          }),
          ledgerItem({ id: 2, description: "Code redeemed", amount_cr: "35" }),
          ledgerItem({ id: 1, description: "Welcome tokens", amount_cr: "500" }),
        ],
        next_cursor: null,
      }),
    });

    const activity = within(
      (await screen.findByRole("heading", { name: "Activity" })).closest("section")!,
    );
    const rows = await activity.findAllByRole("listitem");
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("Voucher: Coffee");
    expect(rows[0]).toHaveTextContent("4 Oct 2026, 12:00");
    // Tokens out are not red: a voucher lowering the balance is normal.
    expect(rows[0]).toHaveTextContent(`${MINUS}35 tokens`);
    expect(rows[0]!.querySelector("[data-slot=amount]")).toHaveClass("text-foreground");
    expect(rows[1]).toHaveTextContent("+35 tokens");
    expect(rows[1]!.querySelector("[data-slot=amount]")).toHaveClass("text-yes-text");
    expect(rows[2]).toHaveTextContent("Welcome tokens");
  });

  test("has one h1 that names the screen, and a title for each section", async () => {
    renderWallet();
    await screen.findByText("No vouchers yet.");

    const headings = screen.getAllByRole("heading", { level: 1 });
    expect(headings).toHaveLength(1);
    expect(headings[0]).toHaveTextContent("Wallet");
    expect(headings[0]).toHaveClass("sr-only");
    expect(
      screen.getAllByRole("heading", { level: 2 }).map((heading) => heading.textContent),
    ).toEqual(["Vouchers", "Activity"]);
    expect(screen.getByRole("region", { name: "Vouchers" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Activity" })).toBeInTheDocument();
  });

  test("keeps to the 720 px column from 768 px, with the two actions side by side", async () => {
    setViewport("desktop");
    renderWallet();
    await screen.findByText("No vouchers yet.");

    const main = screen.getByRole("main");
    expect(main).toHaveClass("px-4", "md:max-w-[45rem]");
    expect(main).not.toHaveClass("lg:max-w-[67.5rem]");

    // Two columns as long as two labels fit beside each other.
    const actions = screen.getByRole("button", { name: "Get a voucher" }).parentElement;
    expect(actions).toHaveClass("grid", "grid-cols-[repeat(auto-fit,minmax(9rem,1fr))]");
    expect(actions).toContainElement(screen.getByRole("button", { name: "Redeem code" }));
    // One white button on the screen; the other is the outlined one.
    expect(screen.getByRole("button", { name: "Get a voucher" })).toHaveAttribute(
      "data-variant",
      "primary",
    );
    expect(screen.getByRole("button", { name: "Redeem code" })).toHaveAttribute(
      "data-variant",
      "secondary",
    );
    for (const button of [
      screen.getByRole("button", { name: "Get a voucher" }),
      screen.getByRole("button", { name: "Redeem code" }),
    ]) {
      // 48 px: more than the 44 px a tap target needs.
      expect(button).toHaveClass("h-12");
    }
  });
});

describe("an empty Wallet", () => {
  test("says so in one line for the vouchers, and with the empty cup for the activity", async () => {
    renderWallet();

    const vouchers = await screen.findByText("No vouchers yet.");
    expect(vouchers.closest("section")).toHaveAccessibleName("Vouchers");

    const empty = (await screen.findByText("Nothing here yet.")).closest("[data-slot=empty-state]");
    expect(empty?.closest("section")).toHaveAccessibleName("Activity");
    expect(empty?.querySelector("svg")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show QR" })).not.toBeInTheDocument();
  });
});

describe("a Wallet that could not be read", () => {
  test("shows one banner with the API's message and 'Try again', and keeps the balance", async () => {
    const { api, user } = renderWallet({
      [WALLET.vouchers]: UNAVAILABLE,
      [WALLET.ledger]: UNAVAILABLE,
    });

    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent("This is temporarily unavailable. Try again shortly.");
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(within(balanceCard()).getByText("240 tokens")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Vouchers" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Activity" })).not.toBeInTheDocument();
    expect(skeletons()).toHaveLength(0);

    api.on(WALLET.vouchers, json(200, { items: [voucherItem()] }));
    api.on(WALLET.ledger, json(200, { items: [ledgerItem()], next_cursor: null }));
    await user.click(within(banner).getByRole("button", { name: "Try again" }));

    expect(await screen.findByRole("button", { name: "Show QR" })).toBeInTheDocument();
    expect(await screen.findByText("Monthly tokens")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  test("shows the part that did answer beside the banner for the one that did not", async () => {
    const { api, user } = renderWallet({
      [WALLET.vouchers]: json(200, { items: [voucherItem({ product_name: "Mate" })] }),
      [WALLET.ledger]: apiError(500, "INTERNAL_ERROR", "Something went wrong on our side."),
    });

    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong on our side.");
    expect(await screen.findByText("Mate")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Activity" })).not.toBeInTheDocument();

    api.on(WALLET.ledger, json(200, { items: [], next_cursor: null }));
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByText("Nothing here yet.")).toBeInTheDocument();
    // Only what failed is asked again.
    expect(api.calls(WALLET.vouchers)).toHaveLength(1);
    expect(api.calls(WALLET.ledger)).toHaveLength(2);
  });
});

describe("the activity pages", () => {
  test("asks for the next page with next_cursor when the user reaches the end, and never for a page size", async () => {
    const { api } = renderWallet({
      [WALLET.ledger]: (request) =>
        request.query.get("cursor") === "page-2"
          ? json(200, {
              items: [ledgerItem({ id: 1, description: "Welcome tokens", amount_cr: "500" })],
              next_cursor: null,
            })
          : json(200, {
              items: [ledgerItem({ id: 2, description: "Monthly tokens" })],
              next_cursor: "page-2",
            }),
    });

    expect(await screen.findByText("Monthly tokens")).toBeInTheDocument();
    expect(screen.queryByText("Welcome tokens")).not.toBeInTheDocument();
    expect(api.calls(WALLET.ledger)).toHaveLength(1);

    intersect();

    expect(await screen.findByText("Welcome tokens")).toBeInTheDocument();
    expect(screen.getByText("Monthly tokens")).toBeInTheDocument();
    const second = api.lastCall(WALLET.ledger)!;
    expect(second.query.get("cursor")).toBe("page-2");
    for (const request of api.calls(WALLET.ledger)) {
      expect(request.query.has("limit")).toBe(false);
    }

    // The last page has no cursor: nothing more is asked for.
    intersect();
    await waitFor(() => expect(api.calls(WALLET.ledger)).toHaveLength(2));
  });

  test("a page that failed shows the API's message with 'Try again' and keeps the rows it has", async () => {
    const { api, user } = renderWallet({
      [WALLET.ledger]: (request) =>
        request.query.has("cursor")
          ? UNAVAILABLE
          : json(200, { items: [ledgerItem({ id: 2 })], next_cursor: "page-2" }),
    });
    await screen.findByText("Monthly tokens");

    intersect();

    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent("This is temporarily unavailable. Try again shortly.");
    expect(screen.getByText("Monthly tokens")).toBeInTheDocument();

    api.on(WALLET.ledger, (request) =>
      request.query.has("cursor")
        ? json(200, {
            items: [ledgerItem({ id: 1, description: "Welcome tokens" })],
            next_cursor: null,
          })
        : json(200, { items: [ledgerItem({ id: 2 })], next_cursor: "page-2" }),
    );
    await user.click(within(banner).getByRole("button", { name: "Try again" }));

    expect(await screen.findByText("Welcome tokens")).toBeInTheDocument();
  });
});

describe("'Get a voucher' follows the server's answer", () => {
  test("is disabled with its line when can_get_voucher is false, whatever the balance", async () => {
    renderWallet({}, me({ balance_cr: "999", can_get_voucher: false }));
    await screen.findByText("No vouchers yet.");

    const button = screen.getByRole("button", { name: "Get a voucher" });
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription("You do not have enough tokens for a voucher yet.");
    expect(screen.getByRole("button", { name: "Redeem code" })).toBeEnabled();
  });

  test("is enabled without the line when can_get_voucher is true, whatever the balance", async () => {
    renderWallet({}, me({ balance_cr: "0", can_get_voucher: true }));
    await screen.findByText("No vouchers yet.");

    expect(screen.getByRole("button", { name: "Get a voucher" })).toBeEnabled();
    expect(
      screen.queryByText("You do not have enough tokens for a voucher yet."),
    ).not.toBeInTheDocument();
  });
});
