import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import type { VoucherCodeResponse } from "@/lib/api/schema";
import { voucherCode, voucherItem } from "@/test/fixtures";
import { apiError, deferred, errorPage, json, noAnswer } from "@/test/http";

import { renderWallet, WALLET } from "./testing";

const MATE = "11111111-1111-4111-8111-111111111111";
const COFFEE = "22222222-2222-4222-8222-222222222222";

const TWO_ACTIVE = json(200, {
  items: [
    voucherItem({ id: MATE, product_name: "Mate" }),
    voucherItem({ id: COFFEE, product_name: "Coffee" }),
  ],
});

/** A fresh code for the Mate voucher, as the API answers: with the product the voucher is for. */
const mateCode = (code = "B4T8WN3C", overrides: Partial<VoucherCodeResponse> = {}) =>
  voucherCode({ voucher_id: MATE, product_name: "Mate", code, ...overrides });

/** The same for the Coffee voucher. */
const coffeeCode = (code = "Z6PD4F7K") =>
  voucherCode({ voucher_id: COFFEE, product_name: "Coffee", code });

/** The row of one voucher of the list. */
async function row(product: string) {
  const title = await screen.findByText(product);
  return within(title.closest("li")!);
}

describe("'Show QR'", () => {
  test("asks for a fresh code for that voucher and shows it as the voucher", async () => {
    const { api, user } = renderWallet({
      [WALLET.vouchers]: TWO_ACTIVE,
      [WALLET.code]: json(200, mateCode("B4T8WN3C", { expires_at: "2026-11-20T10:00:00Z" })),
    });

    await user.click((await row("Mate")).getByRole("button", { name: "Show QR" }));

    const view = await screen.findByRole("dialog", { name: "Mate" });
    const sent = api.lastCall(WALLET.code)!;
    expect(sent.path).toBe(`/api/vouchers/${MATE}/code`);
    // The voucher is named by the address alone; nothing else goes along.
    expect(sent.rawBody).toBe("");
    expect(sent.query.toString()).toBe("");

    expect(within(view).getByRole("img", { name: "Voucher QR code" })).toBeInTheDocument();
    expect(within(view).getByText("B4T8-WN3C")).toHaveClass("type-code");
    // "Valid until" is the answer's, not the list's.
    expect(within(view).getByText(/Valid until/)).toHaveTextContent(
      "Valid until 20 Nov 2026, 11:00",
    );
    expect(
      within(view).getByText("Show this at the bar. You earned it, sort of."),
    ).toBeInTheDocument();
    expect(within(view).getByText("Turn up your brightness")).toBeInTheDocument();
    // A fresh code changes nothing in the list.
    expect(api.calls(WALLET.vouchers)).toHaveLength(1);
  });

  test("the view is titled with the answer's product_name, in the style of a section title", async () => {
    const answer = deferred();
    const { user } = renderWallet({
      [WALLET.vouchers]: TWO_ACTIVE,
      [WALLET.code]: answer.handler,
    });

    await user.click((await row("Mate")).getByRole("button", { name: "Show QR" }));

    // Until the answer is there, the row that was tapped names the sheet.
    const sheet = await screen.findByRole("dialog", { name: "Mate" });
    answer.resolve(json(200, mateCode("B4T8WN3C", { product_name: "Club-Mate" })));

    await within(sheet).findByText("B4T8-WN3C");
    expect(sheet).toHaveAccessibleName("Club-Mate");
    expect(within(sheet).getByRole("heading", { name: "Club-Mate" })).toHaveClass("type-h2");
    expect(within(sheet).queryByRole("heading", { name: "Mate" })).not.toBeInTheDocument();
  });

  test("an answer for another voucher is never shown under this one's name", async () => {
    const { client, user } = renderWallet({
      [WALLET.vouchers]: TWO_ACTIVE,
      [WALLET.code]: json(200, coffeeCode("Z6PD4F7K")),
    });

    await user.click((await row("Mate")).getByRole("button", { name: "Show QR" }));

    const sheet = await screen.findByRole("dialog", { name: "Mate" });
    // The request has been answered: nothing of this write is on its way.
    await waitFor(() =>
      expect(client.isMutating({ mutationKey: ["wallet", "voucher-code"] })).toBe(0),
    );
    expect(sheet).not.toHaveTextContent("Z6PD");
    expect(within(sheet).queryByRole("img", { name: "Voucher QR code" })).not.toBeInTheDocument();
    expect(sheet).toHaveAccessibleName("Mate");
  });

  test("the sheet opens with the tap, and is a skeleton of the voucher while the code is on its way", async () => {
    const answer = deferred();
    const { api, user } = renderWallet({
      [WALLET.vouchers]: TWO_ACTIVE,
      [WALLET.code]: answer.handler,
    });

    await user.click((await row("Mate")).getByRole("button", { name: "Show QR" }));

    const sheet = await screen.findByRole("dialog", { name: "Mate" });
    expect(sheet.querySelector("[data-slot=voucher-skeleton]")).toBeInTheDocument();
    expect(within(sheet).queryByRole("img", { name: "Voucher QR code" })).not.toBeInTheDocument();
    expect(api.calls(WALLET.code)).toHaveLength(1);

    answer.resolve(json(200, mateCode("B4T8WN3C")));

    expect(await within(sheet).findByText("B4T8-WN3C")).toBeInTheDocument();
    expect(sheet.querySelector("[data-slot=voucher-skeleton]")).not.toBeInTheDocument();
    expect(api.calls(WALLET.code)).toHaveLength(1);
  });

  test("a sheet closed while the code is on its way: the code is shown to nobody, and no other is asked for meanwhile", async () => {
    const answer = deferred();
    const { api, client, user } = renderWallet({
      [WALLET.vouchers]: TWO_ACTIVE,
      [WALLET.code]: answer.handler,
    });
    const mate = (await row("Mate")).getByRole("button", { name: "Show QR" });
    const coffee = (await row("Coffee")).getByRole("button", { name: "Show QR" });

    await user.click(mate);
    const sheet = await screen.findByRole("dialog", { name: "Mate" });
    await user.click(within(sheet).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    // The request goes on. Its row waits, and two codes are never on their way at once.
    expect(mate).toBeDisabled();
    expect(mate).toHaveAttribute("aria-busy", "true");
    expect(coffee).toBeDisabled();
    expect(coffee).not.toHaveAttribute("aria-busy");
    await user.click(coffee);
    expect(api.calls(WALLET.code)).toHaveLength(1);
    // The rest of the Wallet is not held up by it.
    expect(screen.getByRole("button", { name: "Get a voucher" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Redeem code" })).toBeEnabled();

    answer.resolve(json(200, mateCode("B4T8WN3C")));

    await waitFor(() => expect(coffee).toBeEnabled());
    expect(mate).toBeEnabled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("B4T8");

    // The next tap asks for a code of its own, and the one nobody saw is kept nowhere.
    api.on(WALLET.code, json(200, coffeeCode("Z6PD4F7K")));
    await user.click(coffee);
    const next = await screen.findByRole("dialog", { name: "Coffee" });
    expect(await within(next).findByText("Z6PD-4F7K")).toBeInTheDocument();
    expect(next).not.toHaveTextContent("B4T8");
    const kept = client
      .getMutationCache()
      .getAll()
      .map((request) => request.state.data);
    expect(JSON.stringify(kept)).not.toContain("B4T8");
  });

  test("closing the view returns the focus to the button that opened it", async () => {
    const { user } = renderWallet({
      [WALLET.vouchers]: TWO_ACTIVE,
      [WALLET.code]: json(200, coffeeCode()),
    });
    const coffee = (await row("Coffee")).getByRole("button", { name: "Show QR" });

    await user.click(coffee);
    const view = await screen.findByRole("dialog", { name: "Coffee" });
    await within(view).findByRole("img", { name: "Voucher QR code" });
    await user.click(within(view).getByRole("button", { name: "Done" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(coffee).toHaveFocus());
  });

  test("two taps in one moment are one request", async () => {
    const answer = deferred();
    const { api } = renderWallet({ [WALLET.vouchers]: TWO_ACTIVE, [WALLET.code]: answer.handler });
    const mate = (await row("Mate")).getByRole("button", { name: "Show QR" });
    const coffee = (await row("Coffee")).getByRole("button", { name: "Show QR" });

    // Faster than the screen can disable the buttons: all three in one moment.
    act(() => {
      mate.click();
      mate.click();
      coffee.click();
    });

    await waitFor(() => expect(mate).toBeDisabled());
    expect(api.calls(WALLET.code)).toHaveLength(1);
    answer.resolve(json(200, mateCode("B4T8WN3C")));
    // One sheet, for the voucher that was tapped first.
    const sheet = await screen.findByRole("dialog", { name: "Mate" });
    expect(await within(sheet).findByText("B4T8-WN3C")).toBeInTheDocument();
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    expect(api.calls(WALLET.code)).toHaveLength(1);
  });

  test("every tap gives another code, and closing the view forgets the one it showed", async () => {
    let asked = 0;
    const { api, client, user } = renderWallet({
      [WALLET.vouchers]: TWO_ACTIVE,
      [WALLET.code]: () => {
        asked += 1;
        return json(200, mateCode(asked === 1 ? "B4T8WN3C" : "Z6PD4F7K"));
      },
    });

    await user.click((await row("Mate")).getByRole("button", { name: "Show QR" }));
    const first = await screen.findByRole("dialog", { name: "Mate" });
    expect(within(first).getByText("B4T8-WN3C")).toBeInTheDocument();
    expect(window.localStorage).toHaveLength(0);
    expect(window.sessionStorage).toHaveLength(0);

    await user.click(within(first).getByRole("button", { name: "Done" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(document.body).not.toHaveTextContent("B4T8");
    expect(
      client.getMutationCache().findAll({ mutationKey: ["wallet", "voucher-code"] }),
    ).toHaveLength(0);

    await user.click((await row("Mate")).getByRole("button", { name: "Show QR" }));
    const second = await screen.findByRole("dialog", { name: "Mate" });
    expect(within(second).getByText("Z6PD-4F7K")).toBeInTheDocument();
    expect(second).not.toHaveTextContent("B4T8");
    expect(api.calls(WALLET.code)).toHaveLength(2);
  });

  test("the cross closes the view like 'Done' does, and forgets the code too", async () => {
    const { user } = renderWallet({
      [WALLET.vouchers]: TWO_ACTIVE,
      [WALLET.code]: json(200, coffeeCode("B4T8WN3C")),
    });
    await user.click((await row("Coffee")).getByRole("button", { name: "Show QR" }));
    const view = await screen.findByRole("dialog", { name: "Coffee" });

    await user.click(within(view).getByRole("button", { name: "Close" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(document.body).not.toHaveTextContent("B4T8");
  });
});

describe("'Show QR' for a voucher that takes no code", () => {
  test.each([
    ["CONFLICT", 409, "This voucher has been used or has expired."],
    ["NOT_FOUND", 404, "This does not exist."],
  ] as const)(
    "%s: the API's message in the sheet, and the vouchers are read again",
    async (code, status, message) => {
      const { api, user } = renderWallet({
        [WALLET.vouchers]: TWO_ACTIVE,
        [WALLET.code]: apiError(status, code, message),
      });
      const mate = await row("Mate");
      // What became of the voucher is the list's to say.
      api.on(
        WALLET.vouchers,
        json(200, {
          items: [
            voucherItem({
              id: MATE,
              product_name: "Mate",
              status: "REDEEMED",
              redeemed_at: "2026-10-05T08:02:00Z",
            }),
            voucherItem({ id: COFFEE, product_name: "Coffee" }),
          ],
        }),
      );

      await user.click(mate.getByRole("button", { name: "Show QR" }));

      const sheet = await screen.findByRole("dialog", { name: "Mate" });
      const error = await within(sheet).findByRole("alert");
      expect(error).toHaveTextContent(message);
      expect(error).toHaveAttribute("data-slot", "inline-error");
      expect(within(sheet).queryByRole("img", { name: "Voucher QR code" })).not.toBeInTheDocument();
      // Asking again cannot help, so nothing offers it.
      expect(within(sheet).queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();

      await waitFor(() => expect(api.calls(WALLET.vouchers)).toHaveLength(2));
      await user.click(within(sheet).getAllByRole("button", { name: "Close" })[0]!);
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect((await row("Mate")).getByText("Redeemed")).toBeInTheDocument();
      expect((await row("Mate")).queryByRole("button", { name: "Show QR" })).toBeNull();
      expect((await row("Coffee")).getByRole("button", { name: "Show QR" })).toBeEnabled();
    },
  );
});

describe("'Show QR' without an answer", () => {
  test.each([
    ["no response", () => noAnswer(), "No connection. Try again."],
    [
      "an error page of the proxy",
      () => errorPage(502),
      "This is temporarily unavailable. Try again shortly.",
    ],
    [
      "an error of the server",
      () => apiError(500, "INTERNAL_ERROR", "Something went wrong on our side."),
      "Something went wrong on our side.",
    ],
  ])("%s: the banner with 'Try again', which asks once more", async (_name, failure, message) => {
    const { api, user } = renderWallet({ [WALLET.vouchers]: TWO_ACTIVE, [WALLET.code]: failure });

    await user.click((await row("Mate")).getByRole("button", { name: "Show QR" }));

    const sheet = await screen.findByRole("dialog", { name: "Mate" });
    const banner = await within(sheet).findByRole("alert");
    expect(banner).toHaveTextContent(message);
    expect(within(sheet).queryByRole("img", { name: "Voucher QR code" })).not.toBeInTheDocument();
    // Nothing of the list has changed, so it is not read again.
    expect(api.calls(WALLET.vouchers)).toHaveLength(1);

    api.on(WALLET.code, json(200, mateCode("B4T8WN3C")));
    await user.click(within(banner).getByRole("button", { name: "Try again" }));

    expect(await within(sheet).findByText("B4T8-WN3C")).toBeInTheDocument();
    expect(within(sheet).queryByRole("alert")).not.toBeInTheDocument();
    expect(api.calls(WALLET.code)).toHaveLength(2);
  });

  test("while it asks once more the view is a skeleton of itself again", async () => {
    const { api, user } = renderWallet({ [WALLET.vouchers]: TWO_ACTIVE, [WALLET.code]: noAnswer });
    await user.click((await row("Mate")).getByRole("button", { name: "Show QR" }));
    const sheet = await screen.findByRole("dialog", { name: "Mate" });
    const again = await within(sheet).findByRole("button", { name: "Try again" });
    const answer = deferred();
    api.on(WALLET.code, answer.handler);

    await user.click(again);

    await waitFor(() =>
      expect(sheet.querySelector("[data-slot=voucher-skeleton]")).toBeInTheDocument(),
    );
    expect(within(sheet).queryByRole("alert")).not.toBeInTheDocument();
    answer.resolve(json(200, mateCode("B4T8WN3C")));
    expect(await within(sheet).findByText("B4T8-WN3C")).toBeInTheDocument();
  });

  test("RATE_LIMITED: 'Try again' waits out the time the API names", async () => {
    const { user } = renderWallet({
      [WALLET.vouchers]: TWO_ACTIVE,
      [WALLET.code]: apiError(429, "RATE_LIMITED", "Too many requests. Try again shortly.", {
        retry_after_seconds: 20,
      }),
    });

    await user.click((await row("Coffee")).getByRole("button", { name: "Show QR" }));

    const sheet = await screen.findByRole("dialog", { name: "Coffee" });
    expect(await within(sheet).findByRole("alert")).toHaveTextContent(
      "Too many requests. Try again shortly.",
    );
    expect(within(sheet).getByRole("button", { name: /Try again in \d+ s/ })).toBeDisabled();
  });
});
