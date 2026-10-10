import { act, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { AppShell } from "@/components/shell";
import { BARISTA_RESULT_MS } from "@/lib/design";
import { me, voucherRedeemed } from "@/test/fixtures";
import { apiError, type Handler, json, mockApi, type Route } from "@/test/http";
import { renderWithProviders } from "@/test/render";

import { BaristaScreen } from "./barista-screen";

/* The screen with the scanner as it is: a camera the test supplies, and a detector that
   reads from every frame whatever the test holds in front of it. */

const REDEEM: Route = "POST /api/barista/vouchers/redeem";

const mediaDevices = Object.getOwnPropertyDescriptor(navigator, "mediaDevices");
const stopCamera = vi.fn();
/** What is in front of the camera right now. */
let inSight: string | null = null;

function camera(getUserMedia: () => Promise<unknown>) {
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia: vi.fn(getUserMedia) },
  });
}

const working = async () => ({ getTracks: () => [{ stop: stopCamera }] });

beforeEach(() => {
  inSight = null;
  stopCamera.mockClear();
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
  class FakeDetector {
    static getSupportedFormats = async () => ["qr_code"];
    detect = async () => (inSight === null ? [] : [{ rawValue: inSight, format: "qr_code" }]);
  }
  vi.stubGlobal("BarcodeDetector", FakeDetector);
  // Frames arrive every 200 ms.
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) =>
    window.setTimeout(() => callback(performance.now()), 200),
  );
  vi.stubGlobal("cancelAnimationFrame", (handle: number) => window.clearTimeout(handle));
});

afterEach(() => {
  vi.useRealTimers();
  if (mediaDevices) Object.defineProperty(navigator, "mediaDevices", mediaDevices);
  else Reflect.deleteProperty(navigator, "mediaDevices");
});

function renderBarista(redeem?: Handler) {
  const api = mockApi(redeem === undefined ? {} : { [REDEEM]: redeem });
  const view = renderWithProviders(
    <AppShell>
      <BaristaScreen />
    </AppShell>,
    { me: me({ role: "BARISTA" }) },
  );
  return { api, ...view };
}

/**
 * Lets time pass in steps, so that the screen renders in between as it does on a phone:
 * the scanner is told to look again, sees what is in sight and reports it.
 */
async function passes(milliseconds: number) {
  for (let passed = 0; passed < milliseconds; passed += 250) {
    await act(() => vi.advanceTimersByTimeAsync(250));
  }
}

const tabBar = () => screen.queryByRole("navigation", { name: "Main" });
const result = (verdict: "accepted" | "repeat" | "rejected") =>
  document.querySelector<HTMLElement>(`[data-verdict=${verdict}]`);

describe("with a camera", () => {
  test("the camera view takes the screen, and the tab bar makes room while it is live", async () => {
    camera(working);
    const { unmount } = renderBarista();
    // Until the camera shows a picture the tab bar is where it always is.
    expect(tabBar()).toBeInTheDocument();

    const view = await screen.findByLabelText("Camera");

    await waitFor(() => expect(tabBar()).not.toBeInTheDocument());
    const main = screen.getByRole("main");
    expect(main).toHaveClass("pb-0");
    expect(main).not.toHaveClass("pb-(--tab-bar-space)");
    // The view takes the height that is left instead of being a square.
    const root = view.closest("[data-slot=qr-scanner]");
    expect(root).toHaveClass("flex-1", "min-h-0");
    expect(view.parentElement).toHaveClass("flex-1");
    expect(view.parentElement).not.toHaveClass("aspect-square");
    expect(view.parentElement?.parentElement).toBe(root);
    expect(screen.getByRole("button", { name: "Type the voucher code" })).toBeInTheDocument();
    // The Scan tab is not the way out: the top bar still is.
    expect(screen.getByRole("link", { name: "Profile" })).toBeInTheDocument();

    unmount();
    expect(stopCamera).toHaveBeenCalled();
  });

  test("a voucher held to the camera is sent once, however long it is held there", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    camera(working);
    const { api } = renderBarista(json(200, voucherRedeemed({ product_name: "Coffee" })));
    await screen.findByLabelText("Camera");

    inSight = "7KQ2-M9XA";
    await act(() => vi.advanceTimersByTimeAsync(600));

    await waitFor(() => expect(result("accepted")).toHaveTextContent("Coffee"));
    expect(api.calls(REDEEM)).toHaveLength(1);
    expect(api.lastCall(REDEEM)?.body).toEqual({ code: "7KQ2-M9XA" });

    // The result goes after 2.5 s and the scanner looks again, at the same voucher. A
    // second request would be answered "not valid" now.
    api.on(REDEEM, apiError(400, "CODE_INVALID", "This code is not valid."));
    await act(() => vi.advanceTimersByTimeAsync(BARISTA_RESULT_MS));
    expect(result("accepted")).toBeNull();
    await passes(8_000);
    expect(result("rejected")).toBeNull();
    expect(api.calls(REDEEM)).toHaveLength(1);

    // The next customer's voucher is read at once.
    api.on(REDEEM, json(200, voucherRedeemed({ product_name: "Mate" })));
    inSight = "B4T8-WN3C";
    await passes(1_500);

    await waitFor(() => expect(result("accepted")).toHaveTextContent("Mate"));
    expect(api.calls(REDEEM)).toHaveLength(2);
    expect(api.lastCall(REDEEM)?.body).toEqual({ code: "B4T8-WN3C" });
  });

  test("one voucher held in view for thirty seconds is one redemption, and no red result", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    camera(working);
    const { api } = renderBarista(json(200, voucherRedeemed({ product_name: "Coffee" })));
    await screen.findByLabelText("Camera");

    inSight = "7KQ2-M9XA";
    await act(() => vi.advanceTimersByTimeAsync(600));
    await waitFor(() => expect(result("accepted")).toHaveTextContent("Coffee"));

    // From here on the API would refuse the voucher: it is used up.
    api.on(REDEEM, apiError(400, "CODE_INVALID", "This code is not valid."));
    const seen = { rejected: false, repeat: false };
    for (let held = 0; held < 30_000; held += 250) {
      await act(() => vi.advanceTimersByTimeAsync(250));
      seen.rejected ||= result("rejected") !== null;
      seen.repeat ||= result("repeat") !== null;
    }

    expect(api.calls(REDEEM)).toHaveLength(1);
    expect(seen).toEqual({ rejected: false, repeat: false });
  });

  test("a voucher that is lost for a few seconds and shown again is asked about again, and the API's `repeat` is the third result", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    camera(working);
    const { api } = renderBarista(json(200, voucherRedeemed({ product_name: "Coffee" })));
    await screen.findByLabelText("Camera");

    inSight = "7KQ2-M9XA";
    await act(() => vi.advanceTimersByTimeAsync(600));
    await waitFor(() => expect(result("accepted")).toHaveTextContent("Coffee"));
    await passes(BARISTA_RESULT_MS + 1_000);
    expect(api.calls(REDEEM)).toHaveLength(1);

    // Out of the camera's sight for four seconds: a hand that moved, a phone that turned.
    inSight = null;
    await passes(4_000);
    api.on(
      REDEEM,
      json(
        200,
        voucherRedeemed({
          product_name: "Coffee",
          redeemed_at: "2026-10-05T16:02:00Z",
          repeat: true,
        }),
      ),
    );
    inSight = "7KQ2-M9XA";
    await passes(1_000);

    const shown = await waitFor(() => {
      const element = result("repeat");
      expect(element).not.toBeNull();
      return element!;
    });
    expect(shown).toHaveTextContent("Redeemed a moment ago at this bar");
    expect(shown).toHaveTextContent("18:02");
    expect(shown).toHaveClass("bg-raised", "text-foreground");
    expect(result("rejected")).toBeNull();
    expect(result("accepted")).toBeNull();
    expect(api.calls(REDEEM)).toHaveLength(2);

    // It stays under the camera after that: nothing more is asked.
    await passes(BARISTA_RESULT_MS + 8_000);
    expect(api.calls(REDEEM)).toHaveLength(2);
    expect(result("rejected")).toBeNull();
  });

  test("a voucher shown again after the time in which the API answers its redemption again is refused", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    camera(working);
    const { api } = renderBarista(json(200, voucherRedeemed()));
    await screen.findByLabelText("Camera");

    inSight = "7KQ2-M9XA";
    await act(() => vi.advanceTimersByTimeAsync(600));
    await waitFor(() => expect(result("accepted")).not.toBeNull());
    inSight = null;
    await passes(BARISTA_RESULT_MS + 3_000);

    // Later, or at another bar: the API's answer is the one for any used voucher.
    api.on(REDEEM, apiError(400, "CODE_INVALID", "This code is not valid."));
    inSight = "7KQ2-M9XA";
    await passes(1_000);

    await waitFor(() => expect(result("rejected")).toHaveTextContent("Voucher not valid"));
    expect(result("repeat")).toBeNull();
    expect(api.calls(REDEEM)).toHaveLength(2);
  });

  test("a rejected voucher returns to the scanner, which reads the next one", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    camera(working);
    const { api } = renderBarista(apiError(400, "CODE_INVALID", "This code is not valid."));
    await screen.findByLabelText("Camera");

    inSight = "not a voucher at all";
    await act(() => vi.advanceTimersByTimeAsync(600));
    await waitFor(() => expect(result("rejected")).toHaveTextContent("Voucher not valid"));
    // What the QR held went to the API as it was read; the API is the judge of it.
    expect(api.lastCall(REDEEM)?.body).toEqual({ code: "not a voucher at all" });

    inSight = null;
    await passes(BARISTA_RESULT_MS + 500);
    expect(result("rejected")).toBeNull();

    api.on(REDEEM, json(200, voucherRedeemed()));
    inSight = "7KQ2-M9XA";
    await passes(1_000);

    await waitFor(() => expect(result("accepted")).not.toBeNull());
    expect(api.calls(REDEEM)).toHaveLength(2);
  });
});

describe("without a camera", () => {
  test.each([
    [
      "permission is refused",
      () => camera(async () => Promise.reject(new DOMException("denied", "NotAllowedError"))),
      "Camera access was refused. Allow it in your browser settings, or type the code.",
    ],
    [
      "the device has none",
      () => camera(async () => Promise.reject(new DOMException("none", "NotFoundError"))),
      "No camera is available. Type the code instead.",
    ],
    [
      "the browser gives no access to one",
      () => {
        Reflect.deleteProperty(navigator, "mediaDevices");
        vi.stubGlobal("isSecureContext", true);
      },
      "No camera is available. Type the code instead.",
    ],
    [
      "the page is not served securely",
      () => {
        Reflect.deleteProperty(navigator, "mediaDevices");
        vi.stubGlobal("isSecureContext", false);
      },
      "The camera needs a secure connection (HTTPS). Type the code instead.",
    ],
  ])(
    "when %s the screen says so, and a typed voucher code still works",
    async (_name, arrange, message) => {
      arrange();
      const { api, user } = renderBarista(json(200, voucherRedeemed({ product_name: "Tea" })));

      expect(await screen.findByText(message)).toBeInTheDocument();
      expect(screen.queryByLabelText("Camera")).not.toBeInTheDocument();
      // No camera is live, so the tab bar stays.
      expect(tabBar()).toBeInTheDocument();
      expect(screen.getByRole("main")).not.toHaveClass("pb-0");

      await user.click(screen.getByRole("button", { name: "Type the voucher code" }));
      const sheet = await screen.findByRole("dialog", { name: "Type the voucher code" });
      await user.type(within(sheet).getByLabelText("Voucher code"), "7kq2m9xa");
      await user.click(within(sheet).getByRole("button", { name: "Redeem voucher" }));

      await waitFor(() => expect(result("accepted")).toHaveTextContent("Tea"));
      expect(api.lastCall(REDEEM)?.body).toEqual({ code: "7KQ2-M9XA" });
    },
  );

  test("a failure is told above the scanner's own message, not over it", async () => {
    camera(async () => Promise.reject(new DOMException("denied", "NotAllowedError")));
    const { user } = renderBarista(
      apiError(503, "SERVICE_UNAVAILABLE", "This is temporarily unavailable."),
    );
    await screen.findByText(/Camera access was refused/);

    await user.click(screen.getByRole("button", { name: "Type the voucher code" }));
    await user.type(await screen.findByLabelText("Voucher code"), "7kq2m9xa{Enter}");

    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent("This is temporarily unavailable.");
    expect(banner.parentElement).not.toHaveClass("absolute");
    expect(screen.getByText(/Camera access was refused/)).toBeInTheDocument();
  });
});
