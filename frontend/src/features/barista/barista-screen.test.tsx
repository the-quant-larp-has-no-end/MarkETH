import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import type { QrScannerProps } from "@/components/qr";
import type { UserRole } from "@/lib/api/schema";
import { BARISTA_RESULT_MS } from "@/lib/design";
import { config, me, voucherRedeemed } from "@/test/fixtures";
import {
  apiError,
  deferred,
  errorPage,
  type Handler,
  json,
  mockApi,
  noAnswer,
  type Route,
} from "@/test/http";
import { setViewport } from "@/test/media";
import { renderWithProviders } from "@/test/render";

import { BaristaScreen } from "./barista-screen";

/* The camera is the scanner's own business and is tested with it, and with this screen in
   barista-camera.test.tsx. Here the scanner is a stand-in that reports what the test says
   it read, and shows whether the screen has paused it. */
const scanner = vi.hoisted(() => ({ props: null as QrScannerProps | null }));
vi.mock("@/components/qr", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/qr")>();
  return {
    ...actual,
    QrScanner: (props: QrScannerProps) => {
      scanner.props = props;
      return (
        <div data-testid="scanner" data-paused={String(props.paused ?? false)}>
          <button type="button" onClick={props.onAlternative}>
            {props.alternativeLabel}
          </button>
        </div>
      );
    },
  };
});

const REDEEM: Route = "POST /api/barista/vouchers/redeem";
const INVALID = apiError(400, "CODE_INVALID", "This code is not valid.");

const vibrate = vi.fn();

beforeEach(() => {
  vibrate.mockClear();
  Object.defineProperty(navigator, "vibrate", { configurable: true, value: vibrate });
});

afterEach(() => {
  scanner.props = null;
  Reflect.deleteProperty(navigator, "vibrate");
  vi.useRealTimers();
});

function renderBarista(redeem?: Handler, role: UserRole = "BARISTA") {
  const api = mockApi(redeem === undefined ? {} : { [REDEEM]: redeem });
  return { api, ...renderWithProviders(<BaristaScreen />, { me: me({ role }) }) };
}

/** What the camera read, reported the way the scanner reports it. */
function scan(text: string) {
  act(() => scanner.props?.onScan(text));
}

const paused = () => screen.getByTestId("scanner").getAttribute("data-paused") === "true";
const result = (verdict: "accepted" | "repeat" | "rejected") =>
  document.querySelector<HTMLElement>(`[data-verdict=${verdict}]`);

/** The result of that kind, once it is on screen. */
const shows = (verdict: "accepted" | "repeat" | "rejected") =>
  waitFor(() => {
    const element = result(verdict);
    expect(element).not.toBeNull();
    return element!;
  });

/** The API's answer to a scan of a voucher this barista redeemed a moment ago. */
const REPEATED = json(200, voucherRedeemed({ repeat: true }));

describe("who the scanner is for", () => {
  test.each(["BARISTA", "ADMIN"] as const)("a caller with the role %s sees it", (role) => {
    renderBarista(undefined, role);

    expect(screen.getByTestId("scanner")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Type the voucher code" })).toBeInTheDocument();
  });

  test("anyone else sees the 'not found' screen, and no permission lecture", () => {
    const { api } = renderBarista(undefined, "USER");

    expect(
      screen.getByText("This page does not exist. Not even in the HG basement."),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("scanner")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Type the voucher code" })).toBeNull();
    expect(api.requests).toHaveLength(0);
  });
});

describe("the scanner at rest", () => {
  test("fills the screen below the top bar, names itself in one h1 and asks the API for nothing", () => {
    const { api } = renderBarista();

    const headings = screen.getAllByRole("heading", { level: 1 });
    expect(headings).toHaveLength(1);
    expect(headings[0]).toHaveTextContent("Scan");
    // No gutters and no column of its own: the scanner is the screen.
    const main = screen.getByRole("main");
    expect(main).not.toHaveClass("px-4");
    expect(main).toContainElement(screen.getByTestId("scanner"));
    expect(paused()).toBe(false);
    expect(result("accepted")).toBeNull();
    expect(result("repeat")).toBeNull();
    expect(result("rejected")).toBeNull();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(api.requests).toHaveLength(0);
  });

  test("gives the scanner the room of the screen, and a column from 768 px", () => {
    setViewport("wide");
    renderBarista();

    expect(scanner.props?.fill).toBe(true);
    expect(scanner.props?.alternativeLabel).toBe("Type the voucher code");
    expect(screen.getByTestId("scanner").parentElement).toHaveClass(
      "flex-1",
      "mx-auto",
      "md:max-w-[45rem]",
    );
  });
});

describe("a scan", () => {
  test("is sent as it was read, in the body, and the scanner pauses until the answer is there", async () => {
    const answer = deferred();
    const { api } = renderBarista(answer.handler);

    scan("7KQ2-M9XA");

    await waitFor(() => expect(api.calls(REDEEM)).toHaveLength(1));
    const sent = api.lastCall(REDEEM)!;
    expect(sent.body).toEqual({ code: "7KQ2-M9XA" });
    // A code never travels in an address.
    expect(sent.path).toBe("/api/barista/vouchers/redeem");
    expect(sent.query.toString()).toBe("");
    expect(sent.headers.get("Accept")).toBe("application/json");

    expect(paused()).toBe(true);
    expect(screen.getByRole("status")).toHaveTextContent("Checking the voucher.");
    expect(result("accepted")).toBeNull();

    answer.resolve(json(200, voucherRedeemed()));
    await waitFor(() => expect(result("accepted")).not.toBeNull());
    expect(screen.queryByText("Checking the voucher.")).not.toBeInTheDocument();
  });

  test("two reports in one moment are one request", async () => {
    const answer = deferred();
    const { api } = renderBarista(answer.handler);

    act(() => {
      scanner.props?.onScan("7KQ2-M9XA");
      scanner.props?.onScan("B4T8-WN3C");
    });

    await waitFor(() => expect(paused()).toBe(true));
    expect(api.calls(REDEEM)).toHaveLength(1);
    answer.resolve(json(200, voucherRedeemed()));
    await waitFor(() => expect(result("accepted")).not.toBeNull());
    expect(api.calls(REDEEM)).toHaveLength(1);
  });
});

describe("an accepted voucher", () => {
  test("fills the screen in green and names the product, larger than anything else", async () => {
    renderBarista(json(200, voucherRedeemed({ product_name: "Iced Tea", value_cr: "35" })));

    scan("7KQ2-M9XA");

    const shown = await waitFor(() => {
      const element = result("accepted");
      expect(element).not.toBeNull();
      return element!;
    });
    expect(shown).toHaveClass("fixed", "inset-0", "bg-yes", "text-ink");
    expect(within(shown).getByText("Iced Tea")).toHaveClass("type-display");
    expect(within(shown).getByText("Voucher accepted")).toHaveClass("type-h2");
    const amount = shown.querySelector("[data-slot=amount]");
    expect(amount).toHaveClass("type-number-lg");
    expect(amount).toHaveTextContent("35 tokens");
    expect(within(shown).getByText("Paid in predictions.")).toBeInTheDocument();
    // Only the product is in the largest style.
    expect(shown.querySelectorAll(".type-display")).toHaveLength(1);
    expect(shown.querySelector("svg.lucide-check")).toHaveClass("size-24");
    expect(result("rejected")).toBeNull();
    expect(result("repeat")).toBeNull();
    // It is read out the moment it appears.
    expect(shown.parentElement).toHaveAttribute("aria-live", "assertive");
  });

  test("shows the voucher's amount as the string the API sent", async () => {
    renderBarista(json(200, voucherRedeemed({ value_cr: "999999999999.999999" })));

    scan("7KQ2-M9XA");

    await waitFor(() =>
      expect(result("accepted")?.querySelector("[data-slot=amount]")).toHaveTextContent(
        "999,999,999,999.99 tokens",
      ),
    );
  });

  test("vibrates once, shows for 2.5 s, and then the scanner returns", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderBarista(json(200, voucherRedeemed()));

    scan("7KQ2-M9XA");

    await waitFor(() => expect(result("accepted")).not.toBeNull());
    expect(vibrate).toHaveBeenCalledTimes(1);
    expect(vibrate).toHaveBeenCalledWith(expect.any(Number));
    expect(paused()).toBe(true);

    await act(() => vi.advanceTimersByTimeAsync(BARISTA_RESULT_MS - 100));
    expect(result("accepted")).not.toBeNull();

    await act(() => vi.advanceTimersByTimeAsync(100));
    expect(result("accepted")).toBeNull();
    expect(paused()).toBe(false);
    expect(vibrate).toHaveBeenCalledTimes(1);
  });
});

describe("a voucher that is not valid", () => {
  test.each([
    ["CODE_INVALID", 400, "This code is not valid."],
    ["VALIDATION_ERROR", 422, "The request is not valid."],
  ] as const)("%s fills the screen in red and gives no reason", async (code, status, message) => {
    renderBarista(apiError(status, code, message, { field: "code" }));

    scan("https://09.hackathon.ethz.ch/wallet?code=4F7KQ2M9XAB3");

    const shown = await waitFor(() => {
      const element = result("rejected");
      expect(element).not.toBeNull();
      return element!;
    });
    expect(shown).toHaveClass("fixed", "inset-0", "bg-no", "text-ink");
    expect(within(shown).getByText("Voucher not valid")).toHaveClass("type-display");
    expect(shown.querySelector("svg.lucide-x")).toHaveClass("size-24");
    // Nothing of the API's answer is shown: no message, no field, no code.
    expect(shown.textContent).toBe("Voucher not valid");
    expect(document.body).not.toHaveTextContent(message);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(result("accepted")).toBeNull();
  });

  test("vibrates twice, shows for 2.5 s, and then the scanner returns", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderBarista(INVALID);

    scan("7KQ2-M9XA");

    await waitFor(() => expect(result("rejected")).not.toBeNull());
    expect(vibrate).toHaveBeenCalledTimes(1);
    const pattern = vibrate.mock.calls[0]![0] as number[];
    // Two pulses with a pause between them.
    expect(pattern).toHaveLength(3);

    await act(() => vi.advanceTimersByTimeAsync(BARISTA_RESULT_MS));

    expect(result("rejected")).toBeNull();
    expect(paused()).toBe(false);
  });

  test("works where a phone cannot vibrate", async () => {
    Reflect.deleteProperty(navigator, "vibrate");
    renderBarista(INVALID);

    scan("7KQ2-M9XA");

    await waitFor(() => expect(result("rejected")).not.toBeNull());
  });
});

describe("a voucher this barista redeemed a moment ago", () => {
  test("fills the screen in a colour that is neither green nor red, with the product and the time of the redemption", async () => {
    renderBarista(
      json(
        200,
        voucherRedeemed({
          product_name: "Iced Tea",
          value_cr: "35",
          redeemed_at: "2026-10-05T16:02:41Z",
          repeat: true,
        }),
      ),
    );

    scan("7KQ2-M9XA");

    const shown = await shows("repeat");
    expect(shown).toHaveClass("fixed", "inset-0", "bg-raised", "text-foreground");
    expect(shown).not.toHaveClass("bg-yes");
    expect(shown).not.toHaveClass("bg-no");
    expect(shown).not.toHaveClass("text-ink");
    expect(shown.querySelector("svg.lucide-clock")).toHaveClass("size-24");
    // The product is still the largest thing on the screen.
    expect(within(shown).getByText("Iced Tea")).toHaveClass("type-display");
    expect(shown.querySelectorAll(".type-display")).toHaveLength(1);
    expect(within(shown).getByText("Redeemed a moment ago at this bar")).toHaveClass("type-h2");
    // The time of day alone, in the timezone of /config, whatever the device is set to.
    const time = within(shown).getByText("18:02");
    expect(time.tagName).toBe("TIME");
    expect(time).toHaveClass("type-number-lg");
    expect(time).toHaveAttribute("datetime", "2026-10-05T16:02:41Z");
    expect(shown.querySelector("[data-slot=amount]")).toHaveTextContent("35 tokens");
    expect(
      within(shown).getByText("It counts once. Hand it over if you have not yet."),
    ).toBeInTheDocument();
    // Neither of the other two results, and nothing of theirs.
    expect(result("accepted")).toBeNull();
    expect(result("rejected")).toBeNull();
    expect(shown).not.toHaveTextContent("Voucher accepted");
    expect(shown).not.toHaveTextContent("Paid in predictions.");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  test("is read out when it appears, with its time", async () => {
    renderBarista(REPEATED);

    scan("7KQ2-M9XA");

    const shown = await shows("repeat");
    const region = shown.parentElement!;
    expect(region).toHaveAttribute("aria-live", "assertive");
    expect(region).toHaveAttribute("aria-atomic", "true");
    // The sentence and the time are heard as two words, not as one.
    expect(region.textContent).toContain("Redeemed a moment ago at this bar 10:02");
  });

  test("the answer says which result it is: `repeat` false is the green one, true the third", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { api } = renderBarista(json(200, voucherRedeemed({ repeat: false })));

    scan("7KQ2-M9XA");
    await shows("accepted");
    expect(result("repeat")).toBeNull();

    // The same voucher after it was away: the same fields, and only `repeat` differs.
    await act(() => vi.advanceTimersByTimeAsync(BARISTA_RESULT_MS));
    await act(() => vi.advanceTimersByTimeAsync(5_000));
    api.on(REDEEM, REPEATED);
    scan("7KQ2-M9XA");

    await shows("repeat");
    expect(result("accepted")).toBeNull();
  });

  test("vibrates with one long pulse, shows for 2.5 s, and then the scanner returns", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderBarista(REPEATED);

    scan("7KQ2-M9XA");

    await shows("repeat");
    expect(vibrate).toHaveBeenCalledTimes(1);
    const pulse = vibrate.mock.calls[0]![0] as number;
    // One pulse, and a longer one than the pulse of an accepted voucher.
    expect(typeof pulse).toBe("number");
    expect(paused()).toBe(true);

    await act(() => vi.advanceTimersByTimeAsync(BARISTA_RESULT_MS - 100));
    expect(result("repeat")).not.toBeNull();
    await act(() => vi.advanceTimersByTimeAsync(100));

    expect(result("repeat")).toBeNull();
    expect(paused()).toBe(false);
    expect(vibrate).toHaveBeenCalledTimes(1);
  });

  test("the three results vibrate in three ways: one pulse, two pulses, one long pulse", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { api } = renderBarista(json(200, voucherRedeemed()));

    scan("7KQ2-M9XA");
    await shows("accepted");
    await act(() => vi.advanceTimersByTimeAsync(BARISTA_RESULT_MS));
    api.on(REDEEM, INVALID);
    scan("B4T8-WN3C");
    await shows("rejected");
    await act(() => vi.advanceTimersByTimeAsync(BARISTA_RESULT_MS));
    api.on(REDEEM, REPEATED);
    scan("Z6PD-4F7K");
    await shows("repeat");

    const [accepted, rejected, repeated] = vibrate.mock.calls.map((call) => call[0] as unknown);
    expect(typeof accepted).toBe("number");
    expect(rejected).toHaveLength(3);
    expect(typeof repeated).toBe("number");
    expect(repeated as number).toBeGreaterThan(accepted as number);
  });

  test("shows the voucher's amount as the string the API sent", async () => {
    renderBarista(json(200, voucherRedeemed({ repeat: true, value_cr: "999999999999.999999" })));

    scan("7KQ2-M9XA");

    const shown = await shows("repeat");
    expect(shown.querySelector("[data-slot=amount]")).toHaveTextContent(
      "999,999,999,999.99 tokens",
    );
  });
});

describe("a failure that is no verdict on the voucher", () => {
  test("a scan whose answer was lost keeps its banner, and the next scan of that voucher shows the third result, never the red one", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    // The request redeemed the voucher, and its answer did not arrive.
    const { api } = renderBarista(noAnswer);

    scan("7KQ2-M9XA");

    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent("No connection. Try again.");
    expect(result("rejected")).toBeNull();
    expect(result("repeat")).toBeNull();
    expect(vibrate).not.toHaveBeenCalled();

    // The barista shows the voucher once more. The API knows who redeemed it a moment ago.
    await act(() => vi.advanceTimersByTimeAsync(5_000));
    api.on(REDEEM, json(200, voucherRedeemed({ product_name: "Mate", repeat: true })));
    scan("7KQ2-M9XA");

    const shown = await shows("repeat");
    expect(shown).toHaveTextContent("Mate");
    expect(shown).toHaveTextContent("Redeemed a moment ago at this bar");
    expect(result("rejected")).toBeNull();
    expect(result("accepted")).toBeNull();
    // The banner has made room for the verdict.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(api.calls(REDEEM)).toHaveLength(2);
    expect(api.calls(REDEEM).map((request) => request.body)).toEqual([
      { code: "7KQ2-M9XA" },
      { code: "7KQ2-M9XA" },
    ]);
  });

  test.each([
    ["no response", () => noAnswer(), "No connection. Try again."],
    [
      "an error page of the proxy",
      () => errorPage(502),
      "This is temporarily unavailable. Try again shortly.",
    ],
    [
      "INTERNAL_ERROR",
      () => apiError(500, "INTERNAL_ERROR", "Something went wrong on our side."),
      "Something went wrong on our side.",
    ],
    [
      "SERVICE_UNAVAILABLE",
      () => apiError(503, "SERVICE_UNAVAILABLE", "This is temporarily unavailable."),
      "This is temporarily unavailable.",
    ],
    ["FORBIDDEN", () => apiError(403, "FORBIDDEN", "You may not do this."), "You may not do this."],
    ["NOT_FOUND", () => apiError(404, "NOT_FOUND", "This does not exist."), "This does not exist."],
  ])(
    "%s: the banner over the scanner, never the red screen, and the barista scans again",
    async (_name, failure, message) => {
      const { api } = renderBarista(failure);
      // FORBIDDEN makes the app ask who is calling; the answer is not this test's subject.
      api.on("GET /api/me", json(200, me({ role: "BARISTA" })));

      scan("7KQ2-M9XA");

      const banner = await screen.findByRole("alert");
      expect(banner).toHaveTextContent(message);
      expect(result("rejected")).toBeNull();
      expect(result("accepted")).toBeNull();
      expect(vibrate).not.toHaveBeenCalled();
      // Nothing is sent again by itself; the scanner waits for the next voucher.
      expect(paused()).toBe(false);
      expect(api.calls(REDEEM)).toHaveLength(1);

      // The next scan is a request of its own, and the banner makes room for its result.
      api.on(REDEEM, json(200, voucherRedeemed()));
      scan("B4T8-WN3C");

      await waitFor(() => expect(result("accepted")).not.toBeNull());
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(api.calls(REDEEM)).toHaveLength(2);
    },
  );

  test("RATE_LIMITED: the banner with a countdown, and no scan is sent until the time is over", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { api } = renderBarista(
      apiError(429, "RATE_LIMITED", "Too many requests. Try again shortly.", {
        retry_after_seconds: 10,
      }),
    );

    scan("7KQ2-M9XA");

    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent("Too many requests. Try again shortly.");
    expect(within(banner).getByText(/^Try again in \d+ s$/)).toBeInTheDocument();
    expect(result("rejected")).toBeNull();
    expect(paused()).toBe(true);

    scan("B4T8-WN3C");
    expect(api.calls(REDEEM)).toHaveLength(1);

    await act(() => vi.advanceTimersByTimeAsync(10_000));

    await waitFor(() => expect(paused()).toBe(false));
    expect(screen.queryByText(/^Try again in/)).not.toBeInTheDocument();
    api.on(REDEEM, json(200, voucherRedeemed()));
    scan("B4T8-WN3C");
    await waitFor(() => expect(api.calls(REDEEM)).toHaveLength(2));
  });
});

describe("a voucher that stays in front of the camera", () => {
  test("is not sent again after its answer; another one is, and so is the same one once it has been away", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { api } = renderBarista(json(200, voucherRedeemed()));

    scan("7KQ2-M9XA");
    await waitFor(() => expect(result("accepted")).not.toBeNull());
    await act(() => vi.advanceTimersByTimeAsync(BARISTA_RESULT_MS));
    expect(paused()).toBe(false);

    // The phone is still held to the scanner: it would be answered "not valid" now.
    api.on(REDEEM, INVALID);
    for (let look = 0; look < 6; look += 1) {
      scan("7KQ2-M9XA");
      expect(paused()).toBe(true);
      await act(() => vi.advanceTimersByTimeAsync(600));
      expect(paused()).toBe(false);
    }
    expect(api.calls(REDEEM)).toHaveLength(1);
    expect(result("rejected")).toBeNull();
    expect(vibrate).toHaveBeenCalledTimes(1);

    // The next customer's voucher is a scan of its own, at once.
    api.on(REDEEM, json(200, voucherRedeemed({ product_name: "Mate" })));
    scan("B4T8-WN3C");
    await waitFor(() => expect(api.calls(REDEEM)).toHaveLength(2));
    await waitFor(() => expect(result("accepted")).toHaveTextContent("Mate"));
    await act(() => vi.advanceTimersByTimeAsync(BARISTA_RESULT_MS));

    // The first voucher is shown again after it was away: that is a scan, and it is sent.
    await act(() => vi.advanceTimersByTimeAsync(5_000));
    api.on(REDEEM, INVALID);
    scan("7KQ2-M9XA");
    await waitFor(() => expect(api.calls(REDEEM)).toHaveLength(3));
    await waitFor(() => expect(result("rejected")).not.toBeNull());
  });

  test("is not sent again after a failure either: the barista shows it once more", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { api } = renderBarista(noAnswer);

    scan("7KQ2-M9XA");
    await screen.findByRole("alert");
    expect(paused()).toBe(false);

    for (let look = 0; look < 4; look += 1) {
      scan("7KQ2-M9XA");
      await act(() => vi.advanceTimersByTimeAsync(600));
    }
    expect(api.calls(REDEEM)).toHaveLength(1);
    expect(screen.getByRole("alert")).toHaveTextContent("No connection. Try again.");

    await act(() => vi.advanceTimersByTimeAsync(5_000));
    api.on(REDEEM, json(200, voucherRedeemed()));
    scan("7KQ2-M9XA");

    await waitFor(() => expect(result("accepted")).not.toBeNull());
    expect(api.calls(REDEEM)).toHaveLength(2);
  });

  test("held there for thirty seconds it is one request, one green result and no other", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { api } = renderBarista(json(200, voucherRedeemed()));

    scan("7KQ2-M9XA");
    await shows("accepted");
    await act(() => vi.advanceTimersByTimeAsync(BARISTA_RESULT_MS));

    // Whatever a second request would be answered, none is made.
    api.on(REDEEM, INVALID);
    for (let held = 0; held < 30_000; held += 600) {
      scan("7KQ2-M9XA");
      await act(() => vi.advanceTimersByTimeAsync(600));
      expect(result("rejected")).toBeNull();
      expect(result("repeat")).toBeNull();
    }

    expect(api.calls(REDEEM)).toHaveLength(1);
    expect(vibrate).toHaveBeenCalledTimes(1);
  });

  test("lost for a few seconds and shown again it is a scan, and the API's `repeat` shows the third result, not the red one", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { api } = renderBarista(json(200, voucherRedeemed({ product_name: "Mate" })));

    scan("7KQ2-M9XA");
    await shows("accepted");
    await act(() => vi.advanceTimersByTimeAsync(BARISTA_RESULT_MS));

    // Out of sight for four seconds, then under the camera again.
    await act(() => vi.advanceTimersByTimeAsync(4_000));
    api.on(REDEEM, json(200, voucherRedeemed({ product_name: "Mate", repeat: true })));
    scan("7KQ2-M9XA");

    const shown = await shows("repeat");
    expect(shown).toHaveTextContent("Mate");
    expect(result("accepted")).toBeNull();
    expect(result("rejected")).toBeNull();
    expect(api.calls(REDEEM)).toHaveLength(2);

    // And while it stays there after that, nothing more is sent.
    await act(() => vi.advanceTimersByTimeAsync(BARISTA_RESULT_MS));
    for (let look = 0; look < 6; look += 1) {
      scan("7KQ2-M9XA");
      await act(() => vi.advanceTimersByTimeAsync(600));
    }
    expect(api.calls(REDEEM)).toHaveLength(2);
  });

  test("the time its result covers the view does not count as time away", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { api } = renderBarista(json(200, voucherRedeemed()));

    scan("7KQ2-M9XA");
    await shows("accepted");
    // The result alone is up for longer than a QR may be out of sight.
    await act(() => vi.advanceTimersByTimeAsync(BARISTA_RESULT_MS));
    expect(result("accepted")).toBeNull();

    api.on(REDEEM, INVALID);
    // The scanner needs a moment to read it again, and has it.
    await act(() => vi.advanceTimersByTimeAsync(1_500));
    scan("7KQ2-M9XA");

    expect(paused()).toBe(true);
    expect(api.calls(REDEEM)).toHaveLength(1);
    expect(result("rejected")).toBeNull();
  });

  test("two vouchers that the camera reads in turn are each sent once: a second one does not make it forget the first", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { api } = renderBarista(json(200, voucherRedeemed({ product_name: "Coffee" })));

    scan("7KQ2-M9XA");
    await shows("accepted");
    await act(() => vi.advanceTimersByTimeAsync(BARISTA_RESULT_MS));
    api.on(REDEEM, json(200, voucherRedeemed({ product_name: "Mate" })));
    scan("B4T8-WN3C");
    await waitFor(() => expect(result("accepted")).toHaveTextContent("Mate"));
    await act(() => vi.advanceTimersByTimeAsync(BARISTA_RESULT_MS));

    // The camera reads now one, now the other, for twenty seconds.
    api.on(REDEEM, INVALID);
    for (let look = 0; look < 34; look += 1) {
      scan(look % 2 === 0 ? "7KQ2-M9XA" : "B4T8-WN3C");
      expect(paused()).toBe(true);
      await act(() => vi.advanceTimersByTimeAsync(600));
    }

    expect(api.calls(REDEEM)).toHaveLength(2);
    expect(result("rejected")).toBeNull();
    expect(result("repeat")).toBeNull();
  });

  test("a voucher that is not valid is not sent again while it stays there either", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { api } = renderBarista(INVALID);

    scan("not a voucher at all");
    await shows("rejected");
    await act(() => vi.advanceTimersByTimeAsync(BARISTA_RESULT_MS));

    for (let look = 0; look < 8; look += 1) {
      scan("not a voucher at all");
      await act(() => vi.advanceTimersByTimeAsync(600));
      expect(result("rejected")).toBeNull();
    }
    expect(api.calls(REDEEM)).toHaveLength(1);
    expect(vibrate).toHaveBeenCalledTimes(1);

    // Shown again after it was away, it is refused again.
    await act(() => vi.advanceTimersByTimeAsync(5_000));
    scan("not a voucher at all");
    await shows("rejected");
    expect(api.calls(REDEEM)).toHaveLength(2);
  });
});

describe("a typed voucher code", () => {
  test("the sheet has its close cross, which closes it and sends nothing", async () => {
    const { api, user } = renderBarista();
    await user.click(screen.getByRole("button", { name: "Type the voucher code" }));
    const sheet = await screen.findByRole("dialog", { name: "Type the voucher code" });
    await user.type(within(sheet).getByLabelText("Voucher code"), "7kq2m9xa");

    const cross = within(sheet).getByRole("button", { name: "Close" });
    expect(cross).toBeEnabled();
    expect(cross.querySelector("svg")).toBeInTheDocument();
    await user.click(cross);

    await waitFor(() => expect(sheet).not.toBeInTheDocument());
    expect(api.requests).toHaveLength(0);
    // What was typed is still there when the sheet is opened again.
    await user.click(screen.getByRole("button", { name: "Type the voucher code" }));
    expect(await screen.findByLabelText("Voucher code")).toHaveValue("7KQ2-M9XA");
  });

  test("a typed code of a voucher redeemed a moment ago ends in the third result, and the field is emptied", async () => {
    const { api, user } = renderBarista(
      json(200, voucherRedeemed({ product_name: "Mate", repeat: true })),
    );
    await user.click(screen.getByRole("button", { name: "Type the voucher code" }));
    const sheet = await screen.findByRole("dialog");
    await user.type(within(sheet).getByLabelText("Voucher code"), "7kq2m9xa{Enter}");

    const shown = await shows("repeat");
    expect(shown).toHaveTextContent("Mate");
    expect(result("accepted")).toBeNull();
    expect(api.lastCall(REDEEM)?.body).toEqual({ code: "7KQ2-M9XA" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    vi.useFakeTimers({ shouldAdvanceTime: true });
    await act(() => vi.advanceTimersByTimeAsync(BARISTA_RESULT_MS));
    vi.useRealTimers();
    await user.click(screen.getByRole("button", { name: "Type the voucher code" }));
    expect(await screen.findByLabelText("Voucher code")).toHaveValue("");
  });

  test("opens a sheet with the field of a voucher code, and pauses the scanner meanwhile", async () => {
    const { user } = renderBarista();

    await user.click(screen.getByRole("button", { name: "Type the voucher code" }));

    const sheet = await screen.findByRole("dialog", { name: "Type the voucher code" });
    const field = within(sheet).getByLabelText("Voucher code");
    // Eight characters in groups of four, from /config.
    expect(field).toHaveAttribute("placeholder", "XXXX-XXXX");
    expect(field).toHaveClass("type-code");
    expect(within(sheet).getByRole("button", { name: "Redeem voucher" })).toBeDisabled();
    expect(scanner.props?.paused).toBe(true);

    await user.type(field, "7kq2m9");
    expect(field).toHaveValue("7KQ2-M9");
    expect(within(sheet).getByRole("button", { name: "Redeem voucher" })).toBeDisabled();
    await user.type(field, "xa99");
    expect(field).toHaveValue("7KQ2-M9XA");
    expect(within(sheet).getByRole("button", { name: "Redeem voucher" })).toBeEnabled();
  });

  test("closing the sheet returns the focus to the button that opened it, and the scanner looks again", async () => {
    const { user } = renderBarista();
    const opener = screen.getByRole("button", { name: "Type the voucher code" });

    await user.click(opener);
    const sheet = await screen.findByRole("dialog");
    expect(scanner.props?.paused).toBe(true);
    await user.click(within(sheet).getByRole("button", { name: "Close" }));

    await waitFor(() => expect(sheet).not.toBeInTheDocument());
    await waitFor(() => expect(opener).toHaveFocus());
    expect(scanner.props?.paused).toBe(false);
  });

  test("takes its length from /config", async () => {
    mockApi({});
    const { user } = renderWithProviders(<BaristaScreen />, {
      me: me({ role: "BARISTA" }),
      config: config({ voucher_code_length: 12 }),
    });

    await user.click(screen.getByRole("button", { name: "Type the voucher code" }));

    expect(await screen.findByLabelText("Voucher code")).toHaveAttribute(
      "placeholder",
      "XXXX-XXXX-XXXX",
    );
  });

  test("is sent like a scan and ends in the same result; an accepted code empties the field", async () => {
    const { api, user } = renderBarista(json(200, voucherRedeemed({ product_name: "Mate" })));
    await user.click(screen.getByRole("button", { name: "Type the voucher code" }));
    const sheet = await screen.findByRole("dialog");
    await user.type(within(sheet).getByLabelText("Voucher code"), "7kq2m9xa");

    await user.click(within(sheet).getByRole("button", { name: "Redeem voucher" }));

    await waitFor(() => expect(result("accepted")).toHaveTextContent("Mate"));
    expect(api.lastCall(REDEEM)?.body).toEqual({ code: "7KQ2-M9XA" });
    expect(vibrate).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    // Typing is always a scan of its own, also for the code that was just typed.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await act(() => vi.advanceTimersByTimeAsync(BARISTA_RESULT_MS));
    vi.useRealTimers();
    await user.click(screen.getByRole("button", { name: "Type the voucher code" }));
    expect(await screen.findByLabelText("Voucher code")).toHaveValue("");
  });

  test("the Enter key sends a whole code, and a rejected code stays in the field to be corrected", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { api, user } = renderBarista(INVALID);
    await user.click(screen.getByRole("button", { name: "Type the voucher code" }));
    const field = await screen.findByLabelText("Voucher code");

    await user.type(field, "7kq2{Enter}");
    expect(api.calls(REDEEM)).toHaveLength(0);

    await user.type(field, "m9xb{Enter}");
    await waitFor(() => expect(result("rejected")).not.toBeNull());
    expect(api.lastCall(REDEEM)?.body).toEqual({ code: "7KQ2-M9XB" });

    await act(() => vi.advanceTimersByTimeAsync(BARISTA_RESULT_MS));
    await user.click(screen.getByRole("button", { name: "Type the voucher code" }));
    expect(await screen.findByLabelText("Voucher code")).toHaveValue("7KQ2-M9XB");
  });

  test("makes room for the answer of a scan that was on its way when the sheet was opened", async () => {
    const answer = deferred();
    const { api, user } = renderBarista(answer.handler);
    scan("7KQ2-M9XA");
    await waitFor(() => expect(api.calls(REDEEM)).toHaveLength(1));

    await user.click(screen.getByRole("button", { name: "Type the voucher code" }));
    const sheet = await screen.findByRole("dialog");
    await user.type(within(sheet).getByLabelText("Voucher code"), "b4t8wn3c");
    // Nothing more can be sent while a voucher is being checked.
    expect(within(sheet).getByRole("button", { name: "Redeem voucher" })).toBeDisabled();
    await user.keyboard("{Enter}");
    expect(api.calls(REDEEM)).toHaveLength(1);

    answer.resolve(json(200, voucherRedeemed({ product_name: "Mate" })));

    // The result is never hidden behind a sheet.
    await waitFor(() => expect(result("accepted")).toHaveTextContent("Mate"));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  test("a submission and a second one in the same moment are one request", async () => {
    const answer = deferred();
    const { api, user } = renderBarista(answer.handler);
    await user.click(screen.getByRole("button", { name: "Type the voucher code" }));
    const field = await screen.findByLabelText("Voucher code");
    await user.type(field, "7kq2m9xa");
    const form = field.closest("form")!;

    fireEvent.submit(form);
    fireEvent.submit(form);

    await waitFor(() => expect(api.calls(REDEEM)).toHaveLength(1));
    answer.resolve(json(200, voucherRedeemed()));
    await waitFor(() => expect(result("accepted")).not.toBeNull());
    expect(api.calls(REDEEM)).toHaveLength(1);
  });
});
