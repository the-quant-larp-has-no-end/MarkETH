import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { QrCode } from "./qr-code";
import { QrScanner } from "./qr-scanner";

describe("QrCode", () => {
  test("is an inline SVG on the light tile, drawn in ink, with no raw colour of its own", () => {
    render(<QrCode value="7KQ2M9XA" label="Voucher QR code" />);

    const qr = screen.getByRole("img", { name: "Voucher QR code" });
    expect(qr.tagName.toLowerCase()).toBe("svg");
    expect(qr.parentElement).toHaveClass("bg-foreground", "text-ink", "rounded-lg", "min-w-60");
    const fills = Array.from(qr.querySelectorAll("path")).map((path) => path.getAttribute("fill"));
    expect(fills).toEqual(["transparent", "currentColor"]);
  });

  /**
   * Where the modules of a drawn code lie, in the units of its own grid: the width of the
   * grid, and the first and the last column and row that hold a dark module.
   */
  function modulesOf(qr: Element) {
    const [, , width] = qr.getAttribute("viewBox")!.split(" ").map(Number);
    const dark = qr.querySelectorAll("path")[1]!.getAttribute("d")!;
    const columns: number[] = [];
    const rows: number[] = [];
    // Every run of dark modules in a row is drawn from its left end: `M{x} {y}h{length}`,
    // and `M{x},{y} h{length}` for the run that ends the row.
    for (const [, x, y, length] of dark.matchAll(/M(\d+)[ ,](\d+) ?h(\d+)/g)) {
      columns.push(Number(x), Number(x) + Number(length) - 1);
      rows.push(Number(y));
    }
    return {
      width: width!,
      left: Math.min(...columns),
      right: Math.max(...columns),
      top: Math.min(...rows),
      bottom: Math.max(...rows),
    };
  }

  test.each([
    ["a voucher code", "7KQ2-M9XA", 21],
    ["the link of a code card", "https://09.hackathon.ethz.ch/wallet?code=4F7KQ2M9XAB3", 33],
  ])(
    "the tile keeps four of the code's own modules free on every side: %s",
    (_what, value, modules) => {
      render(<QrCode value={value} label="QR code" />);

      const qr = screen.getByRole("img", { name: "QR code" });
      const drawn = modulesOf(qr);
      // The grid is the code and four modules on either side of it, and the code fills
      // what is between: the margin is counted in modules, whatever size they are drawn at.
      expect(drawn.width).toBe(modules + 8);
      expect([drawn.left, drawn.top]).toEqual([4, 4]);
      expect([drawn.right, drawn.bottom]).toEqual([modules + 3, modules + 3]);
      // The drawing fills the tile from edge to edge: the tile adds no padding of its own,
      // so the margin is the tile's light colour and nothing else.
      expect(qr).toHaveClass("w-full", "h-auto");
      expect(qr.parentElement!.className).not.toMatch(/(^|\s)p[xytrbl]?-/);
    },
  );

  test("repeats the code as text below the tile, in groups of four", () => {
    render(<QrCode value="7KQ2M9XA" label="Voucher QR code" code="7KQ2M9XA" />);

    expect(screen.getByText("7KQ2-M9XA")).toHaveClass("type-code");
  });

  test("can hold a link while the text below shows the code", () => {
    render(
      <QrCode
        value="https://09.hackathon.ethz.ch/wallet?code=4F7KQ2M9XAB3"
        label="Code QR code"
        code="4F7KQ2M9XAB3"
      />,
    );

    expect(screen.getByText("4F7K-Q2M9-XAB3")).toBeInTheDocument();
    expect(screen.queryByText(/hackathon/)).not.toBeInTheDocument();
  });
});

describe("QrScanner", () => {
  const mediaDevices = Object.getOwnPropertyDescriptor(navigator, "mediaDevices");
  const stop = vi.fn();

  function camera(getUserMedia: () => Promise<unknown>) {
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia: vi.fn(getUserMedia) },
    });
  }

  /** A detector that reads `text` from every frame it is shown. */
  function detector(read: () => string | null) {
    const detect = vi.fn(async () => {
      const text = read();
      return text === null ? [] : [{ rawValue: text, format: "qr_code" }];
    });
    class FakeDetector {
      static getSupportedFormats = async () => ["qr_code"];
      detect = detect;
    }
    vi.stubGlobal("BarcodeDetector", FakeDetector);
    return detect;
  }

  beforeEach(() => {
    stop.mockClear();
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    // Frames arrive every 200 ms.
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) =>
      window.setTimeout(() => callback(performance.now()), 200),
    );
    vi.stubGlobal("cancelAnimationFrame", (handle: number) => window.clearTimeout(handle));
  });

  afterEach(() => {
    if (mediaDevices) Object.defineProperty(navigator, "mediaDevices", mediaDevices);
    else Reflect.deleteProperty(navigator, "mediaDevices");
  });

  const stream = () => ({ getTracks: () => [{ stop }] });

  test("always shows the typed alternative", async () => {
    const onAlternative = vi.fn();
    camera(async () => stream());
    detector(() => null);
    render(
      <QrScanner
        onScan={() => {}}
        alternativeLabel="Type the voucher code"
        onAlternative={onAlternative}
      />,
    );

    const alternative = screen.getByRole("button", { name: "Type the voucher code" });
    alternative.click();

    expect(onAlternative).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.getByLabelText("Camera")).toBeInTheDocument());
  });

  test("is a square camera view with a square guide inside it", async () => {
    camera(async () => stream());
    detector(() => null);
    render(
      <QrScanner
        onScan={() => {}}
        alternativeLabel="Type the code instead"
        onAlternative={() => {}}
      />,
    );

    const video = await screen.findByLabelText("Camera");
    expect(document.querySelector("[data-slot=camera-view]")).toHaveClass("aspect-square");
    expect(video).toHaveClass("size-full", "object-cover");
    expect(video).not.toHaveClass("absolute");
    expect(document.querySelector("[data-slot=scan-guide]")).toHaveClass("inset-[15%]");
  });

  test("with fill the view takes the height that is left, the picture covers it, and the guide stays a square in the middle", async () => {
    camera(async () => stream());
    detector(() => null);
    render(
      <QrScanner
        onScan={() => {}}
        alternativeLabel="Type the voucher code"
        onAlternative={() => {}}
        fill
        className="p-4"
      />,
    );

    const video = await screen.findByLabelText("Camera");
    expect(document.querySelector("[data-slot=qr-scanner]")).toHaveClass(
      "min-h-0",
      "flex-1",
      "p-4",
    );
    const view = document.querySelector("[data-slot=camera-view]")!;
    expect(view).toHaveClass("flex-1", "min-h-60");
    expect(view).not.toHaveClass("aspect-square");
    // Laid over the view: a height in per cent would not hold against a parent that grew.
    expect(video).toHaveClass("absolute", "inset-0", "object-cover");
    const guide = document.querySelector("[data-slot=scan-guide]")!;
    expect(guide).toHaveClass("top-1/2", "left-1/2", "size-[min(70vw,45dvh)]");
    expect(guide).not.toHaveClass("inset-[15%]");
    // The typed alternative stays below it.
    expect(screen.getByRole("button", { name: "Type the voucher code" })).toBeInTheDocument();
  });

  test("says so when the page is not served securely, and still offers typing", () => {
    Reflect.deleteProperty(navigator, "mediaDevices");
    vi.stubGlobal("isSecureContext", false);

    render(
      <QrScanner
        onScan={() => {}}
        alternativeLabel="Type the code instead"
        onAlternative={() => {}}
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent(
      "The camera needs a secure connection (HTTPS). Type the code instead.",
    );
    expect(screen.getByRole("button", { name: "Type the code instead" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Camera")).not.toBeInTheDocument();
  });

  test("says clearly when camera permission is refused", async () => {
    camera(async () => {
      throw new DOMException("denied", "NotAllowedError");
    });

    render(
      <QrScanner
        onScan={() => {}}
        alternativeLabel="Type the code instead"
        onAlternative={() => {}}
      />,
    );

    expect(await screen.findByRole("status")).toHaveTextContent(
      "Camera access was refused. Allow it in your browser settings, or type the code.",
    );
    expect(screen.getByRole("button", { name: "Type the code instead" })).toBeInTheDocument();
  });

  test("says when there is no camera", async () => {
    camera(async () => {
      throw new DOMException("none", "NotFoundError");
    });

    render(
      <QrScanner
        onScan={() => {}}
        alternativeLabel="Type the code instead"
        onAlternative={() => {}}
      />,
    );

    expect(await screen.findByRole("status")).toHaveTextContent(
      "No camera is available. Type the code instead.",
    );
  });

  test("reports what it read once, and reads again only after the screen asked for the next scan", async () => {
    vi.useFakeTimers();
    try {
      camera(async () => stream());
      const detect = detector(() => "7KQ2M9XA");
      const onScan = vi.fn();
      const onLiveChange = vi.fn();
      const props = {
        onScan,
        onLiveChange,
        alternativeLabel: "Type the voucher code",
        onAlternative: () => {},
      };
      const { rerender, unmount } = render(<QrScanner {...props} />);

      await act(() => vi.advanceTimersByTimeAsync(500));
      expect(onLiveChange).toHaveBeenLastCalledWith(true);
      expect(onScan).toHaveBeenCalledTimes(1);
      expect(onScan).toHaveBeenCalledWith("7KQ2M9XA");

      // The same QR stays in front of the camera: it is not reported a second time.
      await act(() => vi.advanceTimersByTimeAsync(2_000));
      expect(onScan).toHaveBeenCalledTimes(1);

      // The screen is busy with the scan, and no frame is looked at meanwhile.
      rerender(<QrScanner {...props} paused />);
      const looked = detect.mock.calls.length;
      await act(() => vi.advanceTimersByTimeAsync(2_000));
      expect(detect.mock.calls.length).toBe(looked);

      // It asks for the next scan.
      rerender(<QrScanner {...props} />);
      await act(() => vi.advanceTimersByTimeAsync(500));
      expect(onScan).toHaveBeenCalledTimes(2);

      // Leaving the screen switches the camera off.
      unmount();
      expect(stop).toHaveBeenCalled();
      expect(onLiveChange).toHaveBeenLastCalledWith(false);
    } finally {
      vi.useRealTimers();
    }
  });

  test("ignores a frame without a QR", async () => {
    vi.useFakeTimers();
    try {
      camera(async () => stream());
      let inView: string | null = null;
      detector(() => inView);
      const onScan = vi.fn();
      render(
        <QrScanner
          onScan={onScan}
          alternativeLabel="Type the code instead"
          onAlternative={() => {}}
        />,
      );

      await act(() => vi.advanceTimersByTimeAsync(1_000));
      expect(onScan).not.toHaveBeenCalled();

      inView = "https://09.hackathon.ethz.ch/wallet?code=4F7KQ2M9XAB3";
      await act(() => vi.advanceTimersByTimeAsync(500));
      expect(onScan).toHaveBeenCalledWith("https://09.hackathon.ethz.ch/wallet?code=4F7KQ2M9XAB3");
    } finally {
      vi.useRealTimers();
    }
  });
});
