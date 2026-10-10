import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import type { QrScannerProps } from "@/components/qr";
import { BEAN_DROP_MS, REDUCED_MOTION_QUERY } from "@/lib/design";
import { config, ledgerItem, me } from "@/test/fixtures";
import { apiError, deferred, errorPage, json, mockApi, noAnswer } from "@/test/http";
import { setMediaQuery, setViewport } from "@/test/media";
import { navigation } from "@/test/navigation";
import { renderWithProviders } from "@/test/render";

import { balanceCard, renderWallet, WALLET } from "./testing";
import { WalletScreen } from "./wallet-screen";

/* The camera is the scanner's own business and is tested with it. Here the scanner is a
   stand-in that reports whatever the test says it read. */
const scanner = vi.hoisted(() => ({ props: null as QrScannerProps | null }));
vi.mock("@/components/qr", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/qr")>();
  return {
    ...actual,
    QrScanner: (props: QrScannerProps) => {
      scanner.props = props;
      return (
        <div data-testid="scanner">
          <button type="button" onClick={props.onAlternative}>
            {props.alternativeLabel}
          </button>
        </div>
      );
    },
  };
});

afterEach(() => {
  scanner.props = null;
});

const INVALID = apiError(400, "CODE_INVALID", "This code is not valid.");

type User = ReturnType<typeof renderWallet>["user"];

/** Opens the sheet from the Wallet's button. */
async function openSheet(user: User) {
  await user.click(await screen.findByRole("button", { name: "Redeem code" }));
  return screen.findByRole("dialog", { name: "Redeem code" });
}

/** Opens the sheet and types a whole code. */
async function typeCode(user: User, typed = "4f7kq2m9xab3") {
  const sheet = await openSheet(user);
  await user.type(within(sheet).getByLabelText("Code"), typed);
  return sheet;
}

/** What the scanner read, reported the way the camera would. */
function scan(text: string) {
  act(() => scanner.props?.onScan(text));
}

describe("the redeem sheet", () => {
  test("has the code field, 'Scan QR' and 'Redeem', and sends nothing by being opened", async () => {
    const { api, user } = renderWallet();

    const sheet = await openSheet(user);

    const field = within(sheet).getByLabelText("Code");
    // The shape of a code comes from /config: twelve characters in groups of four.
    expect(field).toHaveAttribute("placeholder", "XXXX-XXXX-XXXX");
    expect(field).toHaveAttribute("autocapitalize", "characters");
    expect(field).toHaveAttribute("autocorrect", "off");
    expect(field).toHaveClass("type-code");
    expect(within(sheet).getByRole("button", { name: "Scan QR" })).toHaveAttribute(
      "data-variant",
      "secondary",
    );
    expect(within(sheet).getByRole("button", { name: "Redeem" })).toHaveAttribute(
      "data-variant",
      "primary",
    );
    expect(api.calls(WALLET.redeem)).toHaveLength(0);
  });

  test("formats the code as it is typed: capitals, a dash after every four, no more than a code has", async () => {
    const { user } = renderWallet();
    const sheet = await openSheet(user);
    const field = within(sheet).getByLabelText("Code");
    const redeem = within(sheet).getByRole("button", { name: "Redeem" });

    await user.type(field, "4f7kq");
    expect(field).toHaveValue("4F7K-Q");
    // Not a whole code yet.
    expect(redeem).toBeDisabled();

    await user.type(field, "2m9 xab3zzzz");
    expect(field).toHaveValue("4F7K-Q2M9-XAB3");
    expect(redeem).toBeEnabled();
  });

  test("turning the phone keeps the code that was typed", async () => {
    const { user } = renderWallet();
    const sheet = await typeCode(user);
    expect(sheet).toHaveAttribute("data-slot", "drawer-content");

    // Across 768 px the bottom sheet becomes a dialog, and its content is built afresh.
    act(() => setViewport("wide"));

    const dialog = await screen.findByRole("dialog", { name: "Redeem code" });
    expect(dialog).toHaveAttribute("data-slot", "dialog-content");
    expect(within(dialog).getByLabelText("Code")).toHaveValue("4F7K-Q2M9-XAB3");
    expect(within(dialog).getByRole("button", { name: "Redeem" })).toBeEnabled();
  });

  test("closing the sheet returns the focus to the button that opened it", async () => {
    const { user } = renderWallet();
    const opener = await screen.findByRole("button", { name: "Redeem code" });

    await user.click(opener);
    const sheet = await screen.findByRole("dialog", { name: "Redeem code" });
    await user.keyboard("{Escape}");

    await waitFor(() => expect(sheet).not.toBeInTheDocument());
    await waitFor(() => expect(opener).toHaveFocus());
  });

  test("takes its length from /config", async () => {
    const profile = me();
    mockApi({
      [WALLET.me]: json(200, profile),
      [WALLET.vouchers]: json(200, { items: [] }),
      [WALLET.ledger]: json(200, { items: [], next_cursor: null }),
    });
    const { user } = renderWithProviders(<WalletScreen />, {
      me: profile,
      config: config({ code_length: 8 }),
    });

    const sheet = await openSheet(user);
    const field = within(sheet).getByLabelText("Code");

    expect(field).toHaveAttribute("placeholder", "XXXX-XXXX");
    await user.type(field, "4f7kq2m9xab3");
    expect(field).toHaveValue("4F7K-Q2M9");
    expect(within(sheet).getByRole("button", { name: "Redeem" })).toBeEnabled();
  });
});

describe("redeeming a code", () => {
  function redeeming() {
    let balance = "240";
    let redeemed = false;
    return renderWallet({
      [WALLET.me]: () => json(200, me({ balance_cr: balance })),
      [WALLET.ledger]: () =>
        json(200, {
          items: redeemed
            ? [ledgerItem({ id: 9, description: "Code redeemed", amount_cr: "35" })]
            : [],
          next_cursor: null,
        }),
      [WALLET.redeem]: () => {
        balance = "275";
        redeemed = true;
        return json(200, { value_cr: "35", balance_cr: "275" });
      },
    });
  }

  test("sends the code alone, as it stands in the field", async () => {
    const { api, user } = redeeming();
    const sheet = await typeCode(user);

    await user.click(within(sheet).getByRole("button", { name: "Redeem" }));

    await waitFor(() => expect(api.calls(WALLET.redeem)).toHaveLength(1));
    const sent = api.lastCall(WALLET.redeem)!;
    expect(sent.body).toEqual({ code: "4F7K-Q2M9-XAB3" });
    expect(sent.query.toString()).toBe("");
  });

  test("closes the sheet with the toast, sets the balance to the answer's and shows its line in the activity", async () => {
    const { user } = redeeming();
    const sheet = await typeCode(user);
    expect(within(balanceCard()).getByText("240 tokens")).toBeInTheDocument();

    await user.click(within(sheet).getByRole("button", { name: "Redeem" }));

    expect(await screen.findByText("+35 tokens", { selector: "[data-title]" })).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(within(balanceCard()).getByText("275 tokens")).toBeInTheDocument();
    expect(await screen.findByText("Code redeemed")).toBeInTheDocument();

    // The field is empty for the next code.
    const again = await openSheet(user);
    expect(within(again).getByLabelText("Code")).toHaveValue("");
    expect(within(again).queryByRole("alert")).not.toBeInTheDocument();
  });

  test("the toast's word follows the string: one token", async () => {
    const { user } = renderWallet({
      [WALLET.redeem]: json(200, { value_cr: "1", balance_cr: "241" }),
    });
    const sheet = await typeCode(user);

    await user.click(within(sheet).getByRole("button", { name: "Redeem" }));

    expect(await screen.findByText("+1 token", { selector: "[data-title]" })).toBeInTheDocument();
  });

  test("plays the bean drop into the balance: the figure waits for the beans to land", async () => {
    setMediaQuery(REDUCED_MOTION_QUERY, false);
    const { user } = redeeming();
    const sheet = await typeCode(user);
    const beans = () => balanceCard().querySelectorAll(".animate-bean-drop");

    // From here the clock is the test's, so that the drop takes its 600 ms however busy
    // the machine is.
    vi.useFakeTimers();
    try {
      fireEvent.click(within(sheet).getByRole("button", { name: "Redeem" }));
      for (let step = 0; step < 50 && beans().length === 0; step += 1) {
        await act(() => vi.advanceTimersByTimeAsync(1));
      }

      expect(beans().length).toBeGreaterThanOrEqual(3);
      expect(beans().length).toBeLessThanOrEqual(5);
      // While they fall the card still shows what was there before.
      expect(within(balanceCard()).getByText("240 tokens")).toBeInTheDocument();

      await act(() => vi.advanceTimersByTimeAsync(BEAN_DROP_MS));

      expect(within(balanceCard()).getByText("275 tokens")).toBeInTheDocument();
      expect(beans()).toHaveLength(0);
    } finally {
      vi.useRealTimers();
    }
  });

  test("no bean falls when the Wallet merely opens", async () => {
    setMediaQuery(REDUCED_MOTION_QUERY, false);
    renderWallet({}, me({ balance_cr: "440" }));

    expect(await within(await waitFor(balanceCard)).findByText("440 tokens")).toBeInTheDocument();
    expect(balanceCard().querySelectorAll(".animate-bean-drop")).toHaveLength(0);
  });

  test("while the request is on its way the button waits, the field is locked and the sheet stays", async () => {
    const answer = deferred();
    const { api, user } = renderWallet({ [WALLET.redeem]: answer.handler });
    const sheet = await typeCode(user);
    const button = within(sheet).getByRole("button", { name: "Redeem" });

    await user.click(button);

    await waitFor(() => expect(button).toHaveAttribute("aria-busy", "true"));
    expect(button).toBeDisabled();
    expect(within(sheet).getByLabelText("Code")).toBeDisabled();
    expect(within(sheet).getByRole("button", { name: "Scan QR" })).toBeDisabled();
    // The close cross stays in its place, disabled: the sheet cannot be closed meanwhile.
    expect(within(sheet).getByRole("button", { name: "Close" })).toBeDisabled();
    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await user.click(button);
    expect(api.calls(WALLET.redeem)).toHaveLength(1);

    answer.resolve(json(200, { value_cr: "35", balance_cr: "275" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(api.calls(WALLET.redeem)).toHaveLength(1);
  });

  test("a tap and the Enter key in one moment are one request", async () => {
    const answer = deferred();
    const { api, user } = renderWallet({ [WALLET.redeem]: answer.handler });
    const sheet = await typeCode(user);
    const form = within(sheet).getByLabelText("Code").closest("form")!;

    fireEvent.submit(form);
    fireEvent.submit(form);
    fireEvent.click(within(sheet).getByRole("button", { name: "Redeem" }));

    await waitFor(() =>
      expect(within(sheet).getByRole("button", { name: "Redeem" })).toBeDisabled(),
    );
    expect(api.calls(WALLET.redeem)).toHaveLength(1);
    answer.resolve(json(200, { value_cr: "35", balance_cr: "275" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(api.calls(WALLET.redeem)).toHaveLength(1);
  });

  test("the Enter key sends a whole code, and nothing while the code is not whole", async () => {
    const { api, user } = redeeming();
    const sheet = await openSheet(user);
    const field = within(sheet).getByLabelText("Code");

    await user.type(field, "4f7k{Enter}");
    expect(api.calls(WALLET.redeem)).toHaveLength(0);

    await user.type(field, "q2m9xab3{Enter}");
    await waitFor(() => expect(api.calls(WALLET.redeem)).toHaveLength(1));
    expect(api.lastCall(WALLET.redeem)?.body).toEqual({ code: "4F7K-Q2M9-XAB3" });
  });
});

describe("a code the API refuses", () => {
  test("CODE_INVALID: the API's sentence under the field, nothing of ours, and the input stays", async () => {
    const { api, user } = renderWallet({ [WALLET.redeem]: INVALID });
    const sheet = await typeCode(user);
    const field = within(sheet).getByLabelText("Code");

    await user.click(within(sheet).getByRole("button", { name: "Redeem" }));

    const error = await within(sheet).findByRole("alert");
    expect(error).toHaveTextContent("This code is not valid.");
    expect(error.textContent).toBe("This code is not valid.");
    expect(field).toHaveAttribute("aria-invalid", "true");
    expect(field).toHaveAccessibleDescription("This code is not valid.");
    expect(field).toHaveValue("4F7K-Q2M9-XAB3");
    expect(within(sheet).getAllByRole("alert")).toHaveLength(1);
    // An error is never only a toast, and a refusal moves no token.
    expect(screen.getByRole("dialog", { name: "Redeem code" })).toBeInTheDocument();
    expect(within(balanceCard()).getByText("240 tokens")).toBeInTheDocument();
    // A refusal is an answer: nothing has to be read again.
    expect(api.calls(WALLET.me)).toHaveLength(1);
    expect(api.calls(WALLET.ledger)).toHaveLength(1);
  });

  test("every code that cannot be redeemed looks the same, whatever the reason behind it", async () => {
    const { api, user } = renderWallet({ [WALLET.redeem]: INVALID });
    const sheet = await typeCode(user, "aaaabbbbcccc");
    const field = within(sheet).getByLabelText("Code");
    const texts: string[] = [];

    for (const typed of ["aaaabbbbcccc", "ddddeeeeffff", "gggghhhhjjjj"]) {
      await user.clear(field);
      await user.type(field, typed);
      await user.click(within(sheet).getByRole("button", { name: "Redeem" }));
      const error = await within(sheet).findByRole("alert");
      // The sheet below the title is the same text every time, apart from the code.
      texts.push(sheet.textContent!.replace(/\s+/g, " "));
      expect(error.textContent).toBe("This code is not valid.");
    }

    expect(new Set(texts).size).toBe(1);
    expect(api.calls(WALLET.redeem)).toHaveLength(3);
  });

  test("the message goes once the code is changed: it was about the code that was sent", async () => {
    const { user } = renderWallet({ [WALLET.redeem]: INVALID });
    const sheet = await typeCode(user);
    await user.click(within(sheet).getByRole("button", { name: "Redeem" }));
    await within(sheet).findByRole("alert");

    await user.type(within(sheet).getByLabelText("Code"), "{Backspace}");

    expect(within(sheet).queryByRole("alert")).not.toBeInTheDocument();
    expect(within(sheet).getByLabelText("Code")).not.toHaveAttribute("aria-invalid");
  });

  test("the field and what the API said stay when the sheet is closed and opened again", async () => {
    const { user } = renderWallet({ [WALLET.redeem]: INVALID });
    const sheet = await typeCode(user);
    await user.click(within(sheet).getByRole("button", { name: "Redeem" }));
    await within(sheet).findByRole("alert");

    await user.click(within(sheet).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const again = await openSheet(user);

    expect(within(again).getByLabelText("Code")).toHaveValue("4F7K-Q2M9-XAB3");
    expect(within(again).getByRole("alert")).toHaveTextContent("This code is not valid.");
  });

  test.each([
    ["LIMIT_EXCEEDED", 409, "You have already redeemed a code of this batch."],
    ["FORBIDDEN", 403, "You created this batch, so you cannot redeem its codes."],
    ["VALIDATION_ERROR", 422, "This is not a code."],
  ] as const)("%s: the API's message under the field, as sent", async (code, status, message) => {
    const { user } = renderWallet({ [WALLET.redeem]: apiError(status, code, message) });
    const sheet = await typeCode(user);

    await user.click(within(sheet).getByRole("button", { name: "Redeem" }));

    const error = await within(sheet).findByRole("alert");
    expect(error.textContent).toBe(message);
    expect(error).toHaveAttribute("data-slot", "inline-error");
    expect(within(sheet).getByLabelText("Code")).toHaveAccessibleDescription(message);
  });

  test("UNAUTHENTICATED: the API's message as sent, and the app asks again who is calling", async () => {
    const { api, user } = renderWallet({
      [WALLET.redeem]: apiError(401, "UNAUTHENTICATED", "Sign in through the ETH portal."),
    });
    const sheet = await typeCode(user);

    await user.click(within(sheet).getByRole("button", { name: "Redeem" }));

    expect(await within(sheet).findByRole("alert")).toHaveTextContent(
      "Sign in through the ETH portal.",
    );
    // Start-up decides what stands in place of the app when /me says the same.
    await waitFor(() => expect(api.calls(WALLET.me)).toHaveLength(2));
    expect(api.calls(WALLET.redeem)).toHaveLength(1);
  });

  test("RATE_LIMITED: the message, a countdown, and 'Redeem' waits out the time the API names", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const { api, user } = renderWallet({
        [WALLET.redeem]: apiError(429, "RATE_LIMITED", "Too many attempts. Try again later.", {
          retry_after_seconds: 30,
        }),
      });
      const sheet = await typeCode(user);
      const button = within(sheet).getByRole("button", { name: "Redeem" });

      await user.click(button);

      expect(await within(sheet).findByRole("alert")).toHaveTextContent(
        "Too many attempts. Try again later.",
      );
      expect(within(sheet).getByText(/^Try again in \d+ s$/)).toBeInTheDocument();
      expect(button).toBeDisabled();

      // Another code does not shorten the wait: it belongs to the caller.
      await user.type(within(sheet).getByLabelText("Code"), "{Backspace}Z");
      expect(within(sheet).getByRole("alert")).toHaveTextContent(
        "Too many attempts. Try again later.",
      );
      expect(button).toBeDisabled();
      await user.keyboard("{Enter}");
      expect(api.calls(WALLET.redeem)).toHaveLength(1);

      await act(() => vi.advanceTimersByTimeAsync(30_000));

      await waitFor(() => expect(button).toBeEnabled());
      expect(within(sheet).queryByText(/^Try again in/)).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("a redemption without an answer", () => {
  test.each([
    ["no response", () => noAnswer(), "No connection. Try again."],
    [
      "an error page of the proxy",
      () => errorPage(502),
      "This is temporarily unavailable. Try again shortly.",
    ],
    [
      "SERVICE_UNAVAILABLE",
      () =>
        apiError(503, "SERVICE_UNAVAILABLE", "This is temporarily unavailable. Try again shortly."),
      "This is temporarily unavailable. Try again shortly.",
    ],
    [
      "INTERNAL_ERROR",
      () => apiError(500, "INTERNAL_ERROR", "Something went wrong on our side."),
      "Something went wrong on our side.",
    ],
  ])(
    "%s: the banner, and 'Redeem' works again once the balance and the activity have answered",
    async (_name, failure, message) => {
      const { api, user } = renderWallet({ [WALLET.redeem]: failure });
      const sheet = await typeCode(user);
      const button = within(sheet).getByRole("button", { name: "Redeem" });
      // The code did count; the balance and the activity say so once they are read again.
      const profile = deferred();
      const ledger = deferred();
      api.on(WALLET.me, profile.handler);
      api.on(WALLET.ledger, ledger.handler);

      await user.click(button);

      const banner = await within(sheet).findByRole("alert");
      expect(banner).toHaveTextContent(message);
      expect(banner).toHaveAttribute("data-slot", "alert");
      // No verdict on the code: the field is not marked, and the input stays.
      const field = within(sheet).getByLabelText("Code");
      expect(field).not.toHaveAttribute("aria-invalid");
      expect(field).toHaveValue("4F7K-Q2M9-XAB3");
      expect(button).toBeDisabled();
      expect(button).not.toHaveAttribute("aria-busy");
      await waitFor(() => expect(api.calls(WALLET.me)).toHaveLength(2));
      await waitFor(() => expect(api.calls(WALLET.ledger)).toHaveLength(2));

      profile.resolve(json(200, me({ balance_cr: "275" })));
      expect(await within(balanceCard()).findByText("275 tokens")).toBeInTheDocument();
      expect(button).toBeDisabled();

      ledger.resolve(
        json(200, { items: [ledgerItem({ description: "Code redeemed" })], next_cursor: null }),
      );
      await waitFor(() => expect(button).toBeEnabled());
      // Nothing was sent a second time by itself, and the sheet can be closed to look.
      expect(api.calls(WALLET.redeem)).toHaveLength(1);
      await user.click(within(sheet).getByRole("button", { name: "Close" }));
      expect(await screen.findByText("Code redeemed")).toBeInTheDocument();
    },
  );

  test("when the balance cannot be read again either, 'Try again' reads it, and only then 'Redeem' works", async () => {
    const { api, user } = renderWallet({ [WALLET.redeem]: noAnswer });
    const sheet = await typeCode(user);
    const button = within(sheet).getByRole("button", { name: "Redeem" });
    api.on(WALLET.me, noAnswer);

    await user.click(button);

    const banner = await within(sheet).findByRole("alert");
    const again = await within(banner).findByRole("button", { name: "Try again" });
    expect(button).toBeDisabled();

    api.on(WALLET.me, json(200, me()));
    await user.click(again);

    await waitFor(() => expect(button).toBeEnabled());
    expect(api.calls(WALLET.redeem)).toHaveLength(1);
  });
});

describe("a code card's link", () => {
  /** Opens the app at an address: what the browser shows, and what the router reports. */
  function openedAt(href: string) {
    window.history.replaceState(null, "", href);
    navigation.go(href);
  }
  const address = () => window.location.pathname + window.location.search;

  afterEach(() => {
    window.history.replaceState(null, "", "/");
  });

  test("opens the sheet with the field filled in, and still waits for the tap", async () => {
    openedAt("/wallet?code=4F7KQ2M9XAB3");
    const { api, user } = renderWallet({
      [WALLET.redeem]: json(200, { value_cr: "35", balance_cr: "275" }),
    });

    const sheet = await screen.findByRole("dialog", { name: "Redeem code" });
    expect(within(sheet).getByLabelText("Code")).toHaveValue("4F7K-Q2M9-XAB3");
    // A filled field alone submits nothing, however long it stands there.
    await waitFor(() => expect(api.calls(WALLET.ledger)).toHaveLength(1));
    expect(api.calls(WALLET.redeem)).toHaveLength(0);

    await user.click(within(sheet).getByRole("button", { name: "Redeem" }));

    await waitFor(() => expect(api.calls(WALLET.redeem)).toHaveLength(1));
    expect(api.lastCall(WALLET.redeem)?.body).toEqual({ code: "4F7K-Q2M9-XAB3" });
  });

  test("the code is out of the address at once, and in no storage", async () => {
    openedAt("/wallet?code=4F7K-Q2M9-XAB3");
    const replaced = vi.spyOn(window.history, "replaceState");
    const pushed = vi.spyOn(window.history, "pushState");

    renderWallet();

    // Before anything has answered, and before anything is tapped.
    expect(address()).toBe("/wallet");
    // The entry is replaced, so no back step leads to the code.
    expect(replaced).toHaveBeenCalledTimes(1);
    expect(pushed).not.toHaveBeenCalled();
    expect(window.history.length).toBe(1);
    expect(await screen.findByRole("dialog", { name: "Redeem code" })).toBeInTheDocument();
    expect(JSON.stringify({ ...window.localStorage })).not.toContain("4F7K");
    expect(JSON.stringify({ ...window.sessionStorage })).not.toContain("4F7K");
    expect(document.cookie).not.toContain("4F7K");
  });

  test("leaves the rest of the address alone", async () => {
    openedAt("/wallet?from=card&code=4F7KQ2M9XAB3#top");

    renderWallet();

    expect(address() + window.location.hash).toBe("/wallet?from=card#top");
    expect(await screen.findByRole("dialog", { name: "Redeem code" })).toBeInTheDocument();
  });

  test("a link without a code opens no sheet", async () => {
    openedAt("/wallet?code=");

    renderWallet();

    await screen.findByText("No vouchers yet.");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(address()).toBe("/wallet");
  });

  test("without a link the Wallet opens no sheet and touches no address", async () => {
    openedAt("/wallet");
    const replaced = vi.spyOn(window.history, "replaceState");

    renderWallet();

    await screen.findByText("No vouchers yet.");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(replaced).not.toHaveBeenCalled();
  });

  test("what is in the link is put into the field as a code, never as it came", async () => {
    openedAt(`/wallet?code=${encodeURIComponent("https://example.org/wallet?code=4F7K")}`);
    const { api, user } = renderWallet({ [WALLET.redeem]: INVALID });

    const sheet = await screen.findByRole("dialog", { name: "Redeem code" });
    const field = within(sheet).getByLabelText("Code");
    // Letters and digits in groups of four, cut to the length of a code: no address.
    expect(field).toHaveValue("HTTP-SEXA-MPLE");

    await user.click(within(sheet).getByRole("button", { name: "Redeem" }));
    await within(sheet).findByRole("alert");
    expect(JSON.stringify(api.lastCall(WALLET.redeem)?.body)).not.toMatch(/https?:|\/\//);
  });
});

describe("'Scan QR' in the redeem sheet", () => {
  test("shows the scanner with its typed alternative, which leads back to the field", async () => {
    const { user } = renderWallet();
    const sheet = await openSheet(user);

    await user.click(within(sheet).getByRole("button", { name: "Scan QR" }));

    expect(within(sheet).getByTestId("scanner")).toBeInTheDocument();
    expect(within(sheet).queryByLabelText("Code")).not.toBeInTheDocument();
    expect(within(sheet).queryByRole("button", { name: "Redeem" })).not.toBeInTheDocument();

    await user.click(within(sheet).getByRole("button", { name: "Type the code instead" }));

    expect(within(sheet).getByLabelText("Code")).toBeInTheDocument();
    expect(within(sheet).queryByTestId("scanner")).not.toBeInTheDocument();
  });

  test("takes the code out of a code card's link, fills the field and sends nothing", async () => {
    const { api, user } = renderWallet({
      [WALLET.redeem]: json(200, { value_cr: "35", balance_cr: "275" }),
    });
    const sheet = await openSheet(user);
    await user.click(within(sheet).getByRole("button", { name: "Scan QR" }));

    scan("https://09.hackathon.ethz.ch/wallet?code=4F7K-Q2M9-XAB3");

    const field = await within(sheet).findByLabelText("Code");
    expect(field).toHaveValue("4F7K-Q2M9-XAB3");
    expect(within(sheet).queryByTestId("scanner")).not.toBeInTheDocument();
    expect(api.calls(WALLET.redeem)).toHaveLength(0);

    // The user still taps "Redeem", and what goes out is the code, never the link.
    await user.click(within(sheet).getByRole("button", { name: "Redeem" }));
    await waitFor(() => expect(api.calls(WALLET.redeem)).toHaveLength(1));
    expect(api.lastCall(WALLET.redeem)?.body).toEqual({ code: "4F7K-Q2M9-XAB3" });
    expect(api.lastCall(WALLET.redeem)?.rawBody).not.toContain("hackathon");
  });

  test("takes what it read as it is when that is no link", async () => {
    const { user } = renderWallet();
    const sheet = await openSheet(user);
    await user.click(within(sheet).getByRole("button", { name: "Scan QR" }));

    scan("4f7k q2m9 xab3");

    expect(await within(sheet).findByLabelText("Code")).toHaveValue("4F7K-Q2M9-XAB3");
  });

  test("a link without a code fills nothing, and the sheet says so", async () => {
    const { api, user } = renderWallet();
    const sheet = await openSheet(user);
    await user.type(within(sheet).getByLabelText("Code"), "4f7k");
    await user.click(within(sheet).getByRole("button", { name: "Scan QR" }));

    scan("https://09.hackathon.ethz.ch/markets/3f6c2f0e");

    const field = await within(sheet).findByLabelText("Code");
    expect(within(sheet).getByRole("alert")).toHaveTextContent("No code in this QR.");
    // What was typed before is still there.
    expect(field).toHaveValue("4F7K");
    expect(api.calls(WALLET.redeem)).toHaveLength(0);

    // The sentence goes with the next thing the user does.
    await user.type(field, "q");
    expect(within(sheet).queryByRole("alert")).not.toBeInTheDocument();
  });

  test("a scan replaces what the API said about the code before", async () => {
    const { user } = renderWallet({ [WALLET.redeem]: INVALID });
    const sheet = await typeCode(user);
    await user.click(within(sheet).getByRole("button", { name: "Redeem" }));
    await within(sheet).findByRole("alert");
    await user.click(within(sheet).getByRole("button", { name: "Scan QR" }));

    scan("T8WN3CB4Z6PD");

    expect(await within(sheet).findByLabelText("Code")).toHaveValue("T8WN-3CB4-Z6PD");
    expect(within(sheet).queryByRole("alert")).not.toBeInTheDocument();
  });

  test("closing the sheet switches the scanner off", async () => {
    const { user } = renderWallet();
    const sheet = await openSheet(user);
    await user.click(within(sheet).getByRole("button", { name: "Scan QR" }));

    await user.click(within(sheet).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const again = await openSheet(user);

    expect(within(again).queryByTestId("scanner")).not.toBeInTheDocument();
    expect(within(again).getByLabelText("Code")).toBeInTheDocument();
  });
});
