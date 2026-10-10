import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import type { QueryClient } from "@tanstack/react-query";
import { describe, expect, test } from "vitest";

import { config, me, voucherIssued, voucherItem, voucherProduct } from "@/test/fixtures";
import { apiError, deferred, errorPage, json, mockApi, noAnswer } from "@/test/http";
import { setViewport } from "@/test/media";
import { renderWithProviders } from "@/test/render";

import { balanceCard, renderWallet, WALLET } from "./testing";
import { WalletScreen } from "./wallet-screen";

const PRODUCTS = json(200, {
  items: [
    voucherProduct({ id: "coffee", name: "Coffee", price_cr: "35", affordable: true }),
    voucherProduct({ id: "tea", name: "Tea", price_cr: "30", affordable: true }),
    voucherProduct({ id: "toblerone", name: "Toblerone", price_cr: "5", affordable: true }),
  ],
});

const NO_ANSWER_BANNER =
  "We did not get an answer. If your voucher was issued, it is in the list below.";

/** What the cache still holds of the requests that issued a voucher. */
const issuesInCache = (client: QueryClient) =>
  client.getMutationCache().findAll({ mutationKey: ["wallet", "issue-voucher"] });

/** Opens the sheet and taps a product: the second step is on screen. */
async function pick(user: ReturnType<typeof renderWallet>["user"], product: string) {
  await user.click(await screen.findByRole("button", { name: "Get a voucher" }));
  const sheet = await screen.findByRole("dialog", { name: "Get a voucher" });
  await user.click(await within(sheet).findByRole("button", { name: new RegExp(product) }));
  return sheet;
}

describe("the product list", () => {
  test("lists the products of the API in its order, each with what it takes", async () => {
    const { api, user } = renderWallet({ [WALLET.products]: PRODUCTS });
    await screen.findByText("No vouchers yet.");
    // Nothing asks for the products before the sheet is opened.
    expect(api.calls(WALLET.products)).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "Get a voucher" }));

    const sheet = await screen.findByRole("dialog", { name: "Get a voucher" });
    const rows = await within(sheet).findAllByRole("listitem");
    expect(rows.map((row) => row.textContent)).toEqual([
      "Coffee3535 tokens",
      "Tea3030 tokens",
      "Toblerone55 tokens",
    ]);
    expect(api.calls(WALLET.products)).toHaveLength(1);
    // A list, not a shop: no picture, no quantity, nothing to put into a basket.
    expect(within(sheet).queryByRole("img")).not.toBeInTheDocument();
    expect(within(sheet).queryByRole("spinbutton")).not.toBeInTheDocument();
  });

  test("disables a product by 'affordable' alone, and keeps it in the list", async () => {
    const { user } = renderWallet(
      {
        [WALLET.products]: json(200, {
          items: [
            // More than the balance, and the server says yes.
            voucherProduct({ id: "coffee", name: "Coffee", price_cr: "35", affordable: true }),
            // Less than the balance, and the server says no.
            voucherProduct({
              id: "toblerone",
              name: "Toblerone",
              price_cr: "5",
              affordable: false,
            }),
          ],
        }),
      },
      me({ balance_cr: "10", can_get_voucher: true }),
    );

    await user.click(await screen.findByRole("button", { name: "Get a voucher" }));
    const sheet = await screen.findByRole("dialog");

    expect(await within(sheet).findByRole("button", { name: /Coffee/ })).toBeEnabled();
    expect(within(sheet).getByRole("button", { name: /Toblerone/ })).toBeDisabled();
  });

  test("shows skeleton rows while the products load", async () => {
    const answer = deferred();
    const { user } = renderWallet({ [WALLET.products]: answer.handler });

    await user.click(await screen.findByRole("button", { name: "Get a voucher" }));
    const sheet = await screen.findByRole("dialog");

    expect(sheet.querySelector("[data-slot=row-skeletons]")?.children).toHaveLength(5);
    answer.resolve(PRODUCTS);
    expect(await within(sheet).findByRole("button", { name: /Coffee/ })).toBeInTheDocument();
    expect(sheet.querySelector("[data-slot=row-skeletons]")).not.toBeInTheDocument();
  });

  test("shows the banner with 'Try again' when the products cannot be read", async () => {
    const { api, user } = renderWallet({
      [WALLET.products]: apiError(503, "SERVICE_UNAVAILABLE", "This is temporarily unavailable."),
    });

    await user.click(await screen.findByRole("button", { name: "Get a voucher" }));
    const sheet = await screen.findByRole("dialog");
    const banner = await within(sheet).findByRole("alert");
    expect(banner).toHaveTextContent("This is temporarily unavailable.");

    api.on(WALLET.products, PRODUCTS);
    await user.click(within(banner).getByRole("button", { name: "Try again" }));

    expect(await within(sheet).findByRole("button", { name: /Coffee/ })).toBeInTheDocument();
  });

  test("says so when there is no product", async () => {
    const { user } = renderWallet({ [WALLET.products]: json(200, { items: [] }) });

    await user.click(await screen.findByRole("button", { name: "Get a voucher" }));

    expect(await screen.findByText("Nothing to get a voucher for right now.")).toBeInTheDocument();
  });

  test("is a dialog from 768 px", async () => {
    setViewport("wide");
    const { user } = renderWallet({ [WALLET.products]: PRODUCTS });

    await user.click(await screen.findByRole("button", { name: "Get a voucher" }));

    expect(await screen.findByRole("dialog", { name: "Get a voucher" })).toHaveAttribute(
      "data-slot",
      "dialog-content",
    );
  });
});

describe("the second step", () => {
  test("says what happens in the API's figures and the days of /config, and sends nothing yet", async () => {
    const { api, user } = renderWallet({ [WALLET.products]: PRODUCTS });

    const sheet = await pick(user, "Coffee");

    expect(
      within(sheet).getByText("Turn 35 tokens into a voucher for Coffee?"),
    ).toBeInTheDocument();
    expect(
      within(sheet).getByText("Valid for 30 days. An unused voucher is refunded."),
    ).toBeInTheDocument();
    expect(within(sheet).getByRole("button", { name: "Get voucher" })).toHaveAttribute(
      "data-variant",
      "primary",
    );
    expect(within(sheet).getByRole("button", { name: "Back" })).toHaveAttribute(
      "data-variant",
      "ghost",
    );
    expect(api.calls(WALLET.issue)).toHaveLength(0);
  });

  test("words follow the strings: one token, one day", async () => {
    const profile = me();
    mockApi({
      [WALLET.me]: json(200, profile),
      [WALLET.vouchers]: json(200, { items: [] }),
      [WALLET.ledger]: json(200, { items: [], next_cursor: null }),
      [WALLET.products]: json(200, {
        items: [voucherProduct({ id: "gum", name: "Chewing Gum", price_cr: "1" })],
      }),
    });
    const { user } = renderWithProviders(<WalletScreen />, {
      me: profile,
      config: config({ voucher_expiry_days: 1 }),
    });

    await user.click(await screen.findByRole("button", { name: "Get a voucher" }));
    await user.click(await screen.findByRole("button", { name: /Chewing Gum/ }));

    expect(
      await screen.findByText("Turn 1 token into a voucher for Chewing Gum?"),
    ).toBeInTheDocument();
    expect(screen.getByText("Valid for 1 day. An unused voucher is refunded.")).toBeInTheDocument();
  });

  test("'Back' returns to the list", async () => {
    const { user } = renderWallet({ [WALLET.products]: PRODUCTS });
    const sheet = await pick(user, "Tea");

    await user.click(within(sheet).getByRole("button", { name: "Back" }));

    expect(await within(sheet).findByRole("button", { name: /Coffee/ })).toBeInTheDocument();
    expect(within(sheet).queryByRole("button", { name: "Get voucher" })).not.toBeInTheDocument();
  });
});

describe("getting a voucher", () => {
  function issuing() {
    let balance = "240";
    let issued = false;
    return renderWallet({
      [WALLET.me]: () => json(200, me({ balance_cr: balance })),
      [WALLET.vouchers]: () => json(200, { items: issued ? [voucherItem()] : [] }),
      [WALLET.products]: PRODUCTS,
      [WALLET.issue]: () => {
        balance = "205";
        issued = true;
        return json(201, voucherIssued({ code: "7KQ2M9XA", balance_cr: "205" }));
      },
    });
  }

  test("sends the product's id and shows the answer as the voucher", async () => {
    const { api, user } = issuing();
    const sheet = await pick(user, "Coffee");

    await user.click(within(sheet).getByRole("button", { name: "Get voucher" }));

    // The product names the view.
    const view = await screen.findByRole("dialog", { name: "Coffee" });
    expect(within(view).getByRole("heading", { name: "Coffee" })).toHaveClass("type-h2");
    expect(api.calls(WALLET.issue)).toHaveLength(1);
    expect(api.lastCall(WALLET.issue)?.body).toEqual({ product_id: "coffee" });

    // The QR tile holds the code; the code is repeated as text in groups of four.
    const qr = within(view).getByRole("img", { name: "Voucher QR code" });
    expect(qr.parentElement).toHaveClass("bg-foreground", "text-ink", "min-w-60");
    expect(within(view).getByText("7KQ2-M9XA")).toHaveClass("type-code");
    expect(within(view).getByText(/Valid until/)).toHaveTextContent(
      "Valid until 3 Nov 2026, 11:00",
    );
    expect(
      within(view).getByText("Show this at the bar. You earned it, sort of."),
    ).toBeInTheDocument();
    expect(within(view).getByText("Turn up your brightness")).toBeInTheDocument();
    expect(within(view).getByRole("button", { name: "Done" })).toBeInTheDocument();
  });

  test("the balance becomes the answer's, and the voucher is in the list behind the sheet", async () => {
    const { user } = issuing();
    const sheet = await pick(user, "Coffee");
    expect(within(balanceCard()).getByText("240 tokens")).toBeInTheDocument();

    await user.click(within(sheet).getByRole("button", { name: "Get voucher" }));
    await screen.findByRole("dialog", { name: "Coffee" });

    expect(await within(balanceCard()).findByText("205 tokens")).toBeInTheDocument();
    // The list is read again; a voucher in it never carries its code.
    const list = () => screen.getByRole("region", { name: "Vouchers", hidden: true });
    await waitFor(() => expect(list()).toHaveTextContent("Coffee"));
    expect(within(list()).getByRole("button", { name: "Show QR", hidden: true })).toBeVisible();
    expect(list()).not.toHaveTextContent("7KQ2");
  });

  test("the code lives on screen only: nothing is stored, and closing the view forgets it", async () => {
    const { api, client, user } = issuing();
    const sheet = await pick(user, "Coffee");
    await user.click(within(sheet).getByRole("button", { name: "Get voucher" }));
    const view = await screen.findByRole("dialog", { name: "Coffee" });
    expect(within(view).getByText("7KQ2-M9XA")).toBeInTheDocument();

    expect(window.localStorage).toHaveLength(0);
    expect(window.sessionStorage).toHaveLength(0);
    expect(document.cookie).toBe("");

    await user.click(within(view).getByRole("button", { name: "Done" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(document.body).not.toHaveTextContent("7KQ2");
    expect(issuesInCache(client)).toHaveLength(0);
    expect(
      JSON.stringify(
        client
          .getQueryCache()
          .getAll()
          .map((query) => query.state.data),
      ),
    )
      // Not in any read either: only the answer of the write ever held it.
      .not.toContain("7KQ2");

    // Another opening starts at the list, and has issued nothing by itself.
    await user.click(screen.getByRole("button", { name: "Get a voucher" }));
    const again = await screen.findByRole("dialog", { name: "Get a voucher" });
    expect(await within(again).findByRole("button", { name: /Coffee/ })).toBeInTheDocument();
    expect(api.calls(WALLET.issue)).toHaveLength(1);
  });

  test("turning the phone keeps the voucher on screen, and issues nothing again", async () => {
    const { api, user } = issuing();
    const sheet = await pick(user, "Coffee");
    await user.click(within(sheet).getByRole("button", { name: "Get voucher" }));
    const drawer = await screen.findByRole("dialog", { name: "Coffee" });
    expect(drawer).toHaveAttribute("data-slot", "drawer-content");
    expect(within(drawer).getByText("7KQ2-M9XA")).toBeInTheDocument();

    // Across 768 px the bottom sheet becomes a dialog, and its content is built afresh.
    act(() => setViewport("wide"));

    const dialog = await screen.findByRole("dialog", { name: "Coffee" });
    expect(dialog).toHaveAttribute("data-slot", "dialog-content");
    expect(within(dialog).getByText("7KQ2-M9XA")).toBeInTheDocument();
    expect(within(dialog).getByRole("img", { name: "Voucher QR code" })).toBeInTheDocument();
    expect(api.calls(WALLET.issue)).toHaveLength(1);
  });

  test("closing the sheet returns the focus to the button that opened it", async () => {
    const { user } = renderWallet({ [WALLET.products]: PRODUCTS });
    const opener = await screen.findByRole("button", { name: "Get a voucher" });

    await user.click(opener);
    const sheet = await screen.findByRole("dialog", { name: "Get a voucher" });
    await user.click(within(sheet).getByRole("button", { name: "Close" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(opener).toHaveFocus());
  });

  test("while the request is on its way the button waits, and the sheet stays", async () => {
    const answer = deferred();
    const { api, user } = renderWallet({
      [WALLET.products]: PRODUCTS,
      [WALLET.issue]: answer.handler,
    });
    const sheet = await pick(user, "Coffee");
    const button = within(sheet).getByRole("button", { name: "Get voucher" });

    await user.click(button);

    await waitFor(() => expect(button).toBeDisabled());
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(within(sheet).getByRole("button", { name: "Back" })).toBeDisabled();
    // No drag, no cross, no Escape: the sheet stays until the answer is there.
    expect(within(sheet).queryByRole("button", { name: "Close" })).not.toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await user.click(button);
    expect(api.calls(WALLET.issue)).toHaveLength(1);

    answer.resolve(json(201, voucherIssued()));
    expect(await screen.findByRole("dialog", { name: "Coffee" })).toBeInTheDocument();
    expect(api.calls(WALLET.issue)).toHaveLength(1);
  });

  test("two taps in one moment are one request", async () => {
    const answer = deferred();
    const { api, user } = renderWallet({
      [WALLET.products]: PRODUCTS,
      [WALLET.issue]: answer.handler,
    });
    const sheet = await pick(user, "Coffee");
    const button = within(sheet).getByRole("button", { name: "Get voucher" });

    // Faster than the screen can disable the button.
    fireEvent.click(button);
    fireEvent.click(button);
    fireEvent.click(button);

    await waitFor(() => expect(button).toBeDisabled());
    expect(api.calls(WALLET.issue)).toHaveLength(1);
    answer.resolve(json(201, voucherIssued()));
    await screen.findByRole("dialog", { name: "Coffee" });
    expect(api.calls(WALLET.issue)).toHaveLength(1);
  });
});

describe("a voucher the API refuses", () => {
  test("INSUFFICIENT_BALANCE: the API's message above the button, and the products are read again", async () => {
    const { api, user } = renderWallet({
      [WALLET.products]: PRODUCTS,
      [WALLET.issue]: apiError(409, "INSUFFICIENT_BALANCE", "Not enough tokens for this.", {
        balance_cr: "20",
        required_cr: "35",
      }),
    });
    const sheet = await pick(user, "Coffee");
    // What the list says after the refusal is what counts.
    api.on(
      WALLET.products,
      json(200, { items: [voucherProduct({ id: "coffee", name: "Coffee", affordable: false })] }),
    );

    await user.click(within(sheet).getByRole("button", { name: "Get voucher" }));

    const error = await within(sheet).findByRole("alert");
    expect(error).toHaveTextContent("Not enough tokens for this.");
    expect(error).toHaveAttribute("data-slot", "inline-error");
    // Nothing of ours beside the message: no figure of the refusal is shown.
    expect(sheet).not.toHaveTextContent("20");
    expect(screen.getByRole("dialog", { name: "Get a voucher" })).toBeInTheDocument();
    expect(
      within(sheet).getByText("Turn 35 tokens into a voucher for Coffee?"),
    ).toBeInTheDocument();

    await waitFor(() => expect(api.calls(WALLET.products)).toHaveLength(2));
    await waitFor(() => expect(api.calls(WALLET.me)).toHaveLength(2));
    await waitFor(() =>
      expect(within(sheet).getByRole("button", { name: "Get voucher" })).toBeDisabled(),
    );
    expect(screen.queryByText(NO_ANSWER_BANNER)).not.toBeInTheDocument();
  });

  test("VALIDATION_ERROR: the API's message above the button, and 'Back' drops it", async () => {
    const { user } = renderWallet({
      [WALLET.products]: PRODUCTS,
      [WALLET.issue]: apiError(422, "VALIDATION_ERROR", "This product is not on the list.", {
        field: "product_id",
      }),
    });
    const sheet = await pick(user, "Tea");

    await user.click(within(sheet).getByRole("button", { name: "Get voucher" }));
    expect(await within(sheet).findByRole("alert")).toHaveTextContent(
      "This product is not on the list.",
    );

    await user.click(within(sheet).getByRole("button", { name: "Back" }));
    await user.click(await within(sheet).findByRole("button", { name: /Coffee/ }));

    expect(within(sheet).queryByRole("alert")).not.toBeInTheDocument();
  });

  test("RATE_LIMITED: the message, and the button waits out the time the API names", async () => {
    const { api, user } = renderWallet({
      [WALLET.products]: PRODUCTS,
      [WALLET.issue]: apiError(429, "RATE_LIMITED", "Too many requests. Try again shortly.", {
        retry_after_seconds: 30,
      }),
    });
    const sheet = await pick(user, "Coffee");

    await user.click(within(sheet).getByRole("button", { name: "Get voucher" }));

    expect(await within(sheet).findByRole("alert")).toHaveTextContent(
      "Too many requests. Try again shortly.",
    );
    expect(within(sheet).getByText(/Try again in \d+ s/)).toBeInTheDocument();
    expect(within(sheet).getByRole("button", { name: "Get voucher" })).toBeDisabled();

    // Another product does not shorten the wait: it belongs to the caller.
    await user.click(within(sheet).getByRole("button", { name: "Back" }));
    await user.click(await within(sheet).findByRole("button", { name: /Tea/ }));

    expect(within(sheet).getByText("Turn 30 tokens into a voucher for Tea?")).toBeInTheDocument();
    expect(within(sheet).getByRole("button", { name: "Get voucher" })).toBeDisabled();
    expect(within(sheet).getByText(/Try again in \d+ s/)).toBeInTheDocument();
    expect(api.calls(WALLET.issue)).toHaveLength(1);
  });
});

describe("an issue without an answer", () => {
  test.each([
    ["no response", () => noAnswer()],
    ["an error page of the proxy", () => errorPage(502)],
    ["an error of the server", () => apiError(500, "INTERNAL_ERROR", "Something went wrong.")],
  ])(
    "%s: the sheet closes, the banner says so, and no other voucher can be got until the list and the balance have answered",
    async (_name, failure) => {
      const { api, user } = renderWallet({ [WALLET.products]: PRODUCTS, [WALLET.issue]: failure });
      const sheet = await pick(user, "Coffee");
      // The voucher was issued after all; the list says so once it has been read again.
      const vouchers = deferred();
      const profile = deferred();
      api.on(WALLET.vouchers, vouchers.handler);
      api.on(WALLET.me, profile.handler);

      await user.click(within(sheet).getByRole("button", { name: "Get voucher" }));

      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(screen.getByRole("status")).toHaveTextContent(NO_ANSWER_BANNER);
      // Never an error dressed as a refusal, and never the red banner for this.
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      const button = screen.getByRole("button", { name: "Get a voucher" });
      expect(button).toBeDisabled();
      await waitFor(() => expect(api.calls(WALLET.vouchers)).toHaveLength(2));
      await waitFor(() => expect(api.calls(WALLET.me)).toHaveLength(2));

      // One of the two is not enough.
      vouchers.resolve(json(200, { items: [voucherItem({ product_name: "Coffee" })] }));
      expect(await screen.findByRole("button", { name: "Show QR" })).toBeInTheDocument();
      expect(button).toBeDisabled();

      profile.resolve(json(200, me({ balance_cr: "205" })));
      await waitFor(() => expect(button).toBeEnabled());
      expect(within(balanceCard()).getByText("205 tokens")).toBeInTheDocument();
      // Nothing was sent a second time by itself.
      expect(api.calls(WALLET.issue)).toHaveLength(1);
      expect(screen.getByRole("status")).toHaveTextContent(NO_ANSWER_BANNER);
    },
  );

  test("when the list cannot be read again either, the button stays disabled until it has been", async () => {
    const { api, user } = renderWallet({ [WALLET.products]: PRODUCTS, [WALLET.issue]: noAnswer });
    const sheet = await pick(user, "Coffee");
    api.on(WALLET.vouchers, noAnswer);

    await user.click(within(sheet).getByRole("button", { name: "Get voucher" }));

    const error = await screen.findByRole("alert");
    expect(error).toHaveTextContent("No connection. Try again.");
    expect(screen.getByRole("status")).toHaveTextContent(NO_ANSWER_BANNER);
    const button = screen.getByRole("button", { name: "Get a voucher" });
    expect(button).toBeDisabled();
    // What was on screen before stays there.
    expect(screen.getByText("No vouchers yet.")).toBeInTheDocument();

    api.on(WALLET.vouchers, json(200, { items: [] }));
    await user.click(within(error).getByRole("button", { name: "Try again" }));

    await waitFor(() => expect(button).toBeEnabled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(api.calls(WALLET.issue)).toHaveLength(1);
  });

  test("the banner goes when the user turns to something else", async () => {
    const { user } = renderWallet({ [WALLET.products]: PRODUCTS, [WALLET.issue]: noAnswer });
    const sheet = await pick(user, "Coffee");
    await user.click(within(sheet).getByRole("button", { name: "Get voucher" }));
    await screen.findByText(NO_ANSWER_BANNER);
    const button = screen.getByRole("button", { name: "Get a voucher" });
    await waitFor(() => expect(button).toBeEnabled());

    await user.click(button);

    const again = await screen.findByRole("dialog", { name: "Get a voucher" });
    // It starts at the list, without what the last request ended with.
    expect(await within(again).findByRole("button", { name: /Coffee/ })).toBeInTheDocument();
    expect(screen.queryByText(NO_ANSWER_BANNER)).not.toBeInTheDocument();
    expect(within(again).queryByRole("alert")).not.toBeInTheDocument();
  });
});
