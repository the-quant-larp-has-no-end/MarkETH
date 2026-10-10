import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { voucherCode, voucherIssued, voucherItem, voucherProduct } from "@/test/fixtures";
import { apiError, deferred, json, noAnswer } from "@/test/http";
import { setViewport } from "@/test/media";

import { renderWallet, WALLET } from "./testing";

/* DESIGN.md §12: every sheet has the close cross at its top right, in each of its states.
   While a request the sheet sent is out, the cross stays in its place, disabled. Here the
   Wallet's three sheets are walked through every state they have, as a bottom sheet and as
   the dialog they are from 768 px. */

const PRODUCTS = json(200, {
  items: [voucherProduct({ id: "coffee", name: "Coffee", price_cr: "35", affordable: true })],
});
const ONE_ACTIVE = json(200, { items: [voucherItem()] });

/** The cross of the one sheet that is open: a button named "Close" that shows a cross. */
function cross(): HTMLElement {
  const sheet = screen.getByRole("dialog");
  const found = within(sheet)
    .getAllByRole("button", { name: "Close" })
    .filter((button) => button.matches("[data-slot=dialog-close], [data-slot=drawer-close]"));
  expect(found).toHaveLength(1);
  expect(found[0]!.querySelector("svg")).toBeInTheDocument();
  return found[0]!;
}

describe.each(["phone", "wide"] as const)("the close cross of the Wallet's sheets (%s)", (form) => {
  test("'Get a voucher': at the list, at the question, while the voucher is on its way, and on the voucher", async () => {
    setViewport(form);
    const answer = deferred();
    const { user } = renderWallet({ [WALLET.products]: PRODUCTS, [WALLET.issue]: answer.handler });

    // Step 1: the products.
    await user.click(await screen.findByRole("button", { name: "Get a voucher" }));
    const sheet = await screen.findByRole("dialog", { name: "Get a voucher" });
    expect(cross()).toBeEnabled();

    // Step 2: the question.
    await user.click(await within(sheet).findByRole("button", { name: /Coffee/ }));
    expect(within(sheet).getByRole("button", { name: "Get voucher" })).toBeInTheDocument();
    expect(cross()).toBeEnabled();

    // The request is out: the cross stays in its place and cannot be used.
    await user.click(within(sheet).getByRole("button", { name: "Get voucher" }));
    await waitFor(() => expect(cross()).toBeDisabled());

    // The voucher: the cross puts it away, as "Done" does.
    answer.resolve(json(201, voucherIssued({ code: "7KQ2M9XA" })));
    const view = await screen.findByRole("dialog", { name: "Coffee" });
    await within(view).findByRole("img", { name: "Voucher QR code" });
    expect(cross()).toBeEnabled();
    await user.click(cross());

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(document.body).not.toHaveTextContent("7KQ2");
  });

  test("'Get a voucher' after a refusal: the cross closes the sheet", async () => {
    setViewport(form);
    const { user } = renderWallet({
      [WALLET.products]: PRODUCTS,
      [WALLET.issue]: apiError(409, "INSUFFICIENT_BALANCE", "Not enough tokens for this."),
    });
    await user.click(await screen.findByRole("button", { name: "Get a voucher" }));
    const sheet = await screen.findByRole("dialog", { name: "Get a voucher" });
    await user.click(await within(sheet).findByRole("button", { name: /Coffee/ }));
    await user.click(within(sheet).getByRole("button", { name: "Get voucher" }));
    await within(sheet).findByText("Not enough tokens for this.");

    await user.click(cross());

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  test("'Show QR': while the code is on its way, with the code, and with a failure", async () => {
    setViewport(form);
    const answer = deferred();
    const { api, user } = renderWallet({
      [WALLET.vouchers]: ONE_ACTIVE,
      [WALLET.code]: answer.handler,
    });

    // The code is on its way: this sheet can be closed, and its cross works.
    await user.click(await screen.findByRole("button", { name: "Show QR" }));
    const sheet = await screen.findByRole("dialog", { name: "Coffee" });
    expect(sheet.querySelector("[data-slot=voucher-skeleton]")).toBeInTheDocument();
    expect(cross()).toBeEnabled();

    // The code is there.
    answer.resolve(json(200, voucherCode({ code: "B4T8WN3C" })));
    await within(sheet).findByText("B4T8-WN3C");
    expect(cross()).toBeEnabled();
    await user.click(cross());
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(document.body).not.toHaveTextContent("B4T8");

    // No answer: the banner, and the cross.
    api.on(WALLET.code, noAnswer);
    await user.click(await screen.findByRole("button", { name: "Show QR" }));
    const failed = await screen.findByRole("dialog", { name: "Coffee" });
    await within(failed).findByRole("alert");
    expect(cross()).toBeEnabled();
    await user.click(cross());
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    // A voucher that takes no code: the API's message, and the cross.
    api.on(WALLET.code, apiError(409, "CONFLICT", "This voucher has been used or has expired."));
    await waitFor(() => expect(screen.getByRole("button", { name: "Show QR" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "Show QR" }));
    const gone = await screen.findByRole("dialog", { name: "Coffee" });
    await within(gone).findByText("This voucher has been used or has expired.");
    expect(cross()).toBeEnabled();
    await user.click(cross());
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  test("'Redeem code': at the field, at the scanner, while the code is on its way, and after a refusal", async () => {
    setViewport(form);
    const answer = deferred();
    const { user } = renderWallet({ [WALLET.redeem]: answer.handler });

    // The field.
    await user.click(await screen.findByRole("button", { name: "Redeem code" }));
    const sheet = await screen.findByRole("dialog", { name: "Redeem code" });
    expect(cross()).toBeEnabled();

    // The scanner.
    await user.click(within(sheet).getByRole("button", { name: "Scan QR" }));
    expect(
      within(sheet).getByRole("button", { name: "Type the code instead" }),
    ).toBeInTheDocument();
    expect(cross()).toBeEnabled();
    await user.click(within(sheet).getByRole("button", { name: "Type the code instead" }));

    // The request is out: the cross stays in its place and cannot be used.
    await user.type(within(sheet).getByLabelText("Code"), "4f7kq2m9xab3");
    await user.click(within(sheet).getByRole("button", { name: "Redeem" }));
    await waitFor(() => expect(cross()).toBeDisabled());

    // Refused: the cross works again and closes the sheet.
    answer.resolve(apiError(400, "CODE_INVALID", "This code is not valid."));
    await within(sheet).findByText("This code is not valid.");
    await waitFor(() => expect(cross()).toBeEnabled());
    await user.click(cross());

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});
