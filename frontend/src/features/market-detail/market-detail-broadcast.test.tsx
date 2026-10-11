import { act, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { sizeCharts } from "@/test/charts";
import { MARKET_ID, marketDetail, marketViewer, TODAY } from "@/test/fixtures";
import { deferred, mockApi } from "@/test/http";
import { setViewport } from "@/test/media";
import { followHistoryWrites, navigation } from "@/test/navigation";
import { watchScrolling } from "@/test/observers";
import { renderWithProviders } from "@/test/render";

import { MarketDetailScreen } from "./index";
import { liveStream, MARKET, mockMarketApi, PATH, resetTrade } from "./testing";

vi.mock("@/features/broadcast", async () => (await import("./testing")).broadcastFeature);
vi.mock("@/features/trade", async () => (await import("./testing")).tradeFeature);

/**
 * The live stream of a market on Market detail (DESIGN.md §13): where its section stands,
 * what the screen gives it, and what the screen does with its outcome buttons while the
 * viewer watches on a phone. The section itself is a stand-in here; the real one inside
 * the real screen is `market-detail-with-broadcast.test.tsx`.
 */

const QUESTION = "Will the Polybahn run without interruption during the exam session?";

type Market = ReturnType<typeof marketDetail>;

async function open(market: Market = marketDetail()) {
  const api = mockMarketApi(market);
  const view = renderWithProviders(<MarketDetailScreen marketId={MARKET_ID} />);
  await screen.findByRole("heading", { level: 1, name: QUESTION });
  return { api, ...view };
}

const halted = () =>
  marketDetail({
    status: "HALTED",
    trading_open: false,
    viewer: marketViewer({ can_trade: false, cannot_trade_reason: "MARKET_NOT_OPEN" }),
  });

const closed = () =>
  marketDetail({
    status: "CLOSED",
    trading_open: false,
    viewer: marketViewer({ can_trade: false, cannot_trade_reason: "MARKET_NOT_OPEN" }),
  });

const section = () => screen.getByTestId("live-stream");
const place = () => document.querySelector<HTMLElement>("[data-slot=outcome-buttons-place]")!;
const pinned = () => document.querySelector<HTMLElement>("[data-slot=pinned]");

/** The room of the tab bar is the safe area of the device alone. */
const WITHOUT_TAB_BAR = "[--tab-bar-space:env(safe-area-inset-bottom)]";

/** `first` stands before `second` on the page. */
const before = (first: Element, second: Element) =>
  (first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;

const inTheirPlace = () => {
  expect(pinned()).not.toBeInTheDocument();
  expect(within(place()).getByRole("button", { name: "YES 54.8%" })).toBeInTheDocument();
  expect(within(place()).getByRole("button", { name: "NO 45.2%" })).toBeInTheDocument();
};

const pinnedBelow = () => {
  const bar = pinned();
  expect(bar).toBeInTheDocument();
  expect(within(bar!).getByRole("button", { name: "YES 54.8%" })).toBeEnabled();
  expect(within(bar!).getByRole("button", { name: "NO 45.2%" })).toBeEnabled();
  // One pair of buttons: the place keeps its room and is empty.
  expect(place()).toBeEmptyDOMElement();
  expect(place()).toHaveClass("min-h-12");
};

/**
 * An `IntersectionObserver` that is told how much of an element is on screen, for what
 * `watchScrolling()` does not say: an element that is cut by an edge of the screen.
 */
function watchParts() {
  const observers: { callback: IntersectionObserverCallback; targets: Set<Element> }[] = [];

  class PartObserver {
    private readonly entry: (typeof observers)[number];

    constructor(callback: IntersectionObserverCallback) {
      this.entry = { callback, targets: new Set() };
      observers.push(this.entry);
    }

    observe(target: Element) {
      this.entry.targets.add(target);
    }

    unobserve(target: Element) {
      this.entry.targets.delete(target);
    }

    disconnect() {
      this.entry.targets.clear();
    }

    takeRecords() {
      return [];
    }
  }

  vi.stubGlobal("IntersectionObserver", PartObserver);

  return {
    /** `ratio` of the element lies in what the observer looks at. */
    shows(target: Element, ratio: number) {
      for (const { callback, targets } of observers) {
        if (!targets.has(target)) continue;
        const record = { target, isIntersecting: ratio > 0, intersectionRatio: ratio };
        callback([record as IntersectionObserverEntry], {} as IntersectionObserver);
      }
    },
  };
}

/**
 * Says how far down the things reach that stick at the top of the screen, in CSS pixels:
 * the top bar, and the video box of the live stream, which the section puts under it
 * while its viewer watches on a phone. jsdom lays nothing out; the chart keeps the size
 * `sizeCharts()` gives it.
 */
function layOut(bottoms: { topBar: number; box: number }) {
  const rect = (width: number, height: number) =>
    ({ x: 0, y: 0, top: 0, left: 0, right: width, bottom: height, width, height }) as DOMRect;

  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (
    this: HTMLElement,
  ) {
    if (this.classList.contains("recharts-responsive-container")) return rect(343, 200);
    const slot = this.getAttribute("data-slot");
    if (slot === "top-bar") return rect(375, bottoms.topBar);
    if (slot === "live-stream-box") return rect(343, bottoms.box);
    return rect(0, 0);
  });
  return bottoms;
}

/** The section's video box, as it sticks under the top bar while its viewer watches. */
function showBox() {
  const box = document.createElement("div");
  box.setAttribute("data-slot", "live-stream-box");
  document.body.append(box);
  return box;
}

beforeEach(() => {
  navigation.go(PATH);
  followHistoryWrites();
  sizeCharts();
  resetTrade();
  vi.useFakeTimers({ now: TODAY, toFake: ["Date"] });
});

afterEach(() => {
  vi.useRealTimers();
  document.querySelector("body > [data-slot=live-stream-box]")?.remove();
});

describe("MarketDetailScreen and the live stream of its market", () => {
  describe("the section", () => {
    test("stands between the Price and the banner of the status, above the chart", async () => {
      await open(halted());

      const price = document.querySelector("[data-slot=price]")!;
      const banner = screen.getByText("Trading is paused.");
      const chart = await screen.findByRole("figure");

      expect(price).toBeInTheDocument();
      expect(before(price, section())).toBe(true);
      expect(before(section(), banner)).toBe(true);
      expect(before(banner, chart)).toBe(true);
      expect(before(chart, place())).toBe(true);
    });

    test("stands in the column of the screen itself, with nothing around it", async () => {
      await open();

      // Its video box sticks in the column it stands in, and heads it while it does.
      const column = screen.getByRole("heading", { level: 1 }).closest("header")!.parentElement!;
      expect(column).toHaveClass("flex", "flex-col", "gap-6");
      expect(section().parentElement).toBe(column);
      expect(place().parentElement).toBe(column);
    });

    test("is given the id of the market", async () => {
      await open();

      expect(section()).toHaveAttribute("data-market", MARKET_ID);
    });

    test.each([
      ["open", marketDetail(), "yes"],
      ["paused", halted(), "yes"],
      ["closed", closed(), "no"],
      ["resolved", marketDetail({ status: "RESOLVED", trading_open: false, outcome: "YES" }), "no"],
    ])(
      "is told whether the screen pins outcome buttons: a market that is %s",
      async (_, market, told) => {
        await open(market);

        expect(section()).toHaveAttribute("data-pinned-buttons", told);
      },
    );

    test.each(["PROPOSED", "REJECTED", "EXPIRED"] as const)(
      "is not there for a proposal (%s): a proposal has no live stream",
      async (status) => {
        await open(marketDetail({ status, trading_open: false }));

        expect(screen.queryByTestId("live-stream")).not.toBeInTheDocument();
      },
    );

    test("is not there while the market loads", () => {
      mockApi({ [MARKET]: deferred().handler });
      renderWithProviders(<MarketDetailScreen marketId={MARKET_ID} />);

      expect(screen.queryByTestId("live-stream")).not.toBeInTheDocument();
    });
  });

  describe("while the viewer watches on a phone, the outcome buttons are on screen at every scroll position", () => {
    test("pinned to the lower edge while their place lies below the screen", async () => {
      const scrolling = watchScrolling();
      await open();
      act(() => scrolling.notReached(place()));
      inTheirPlace();

      act(() => liveStream.watching(true));
      act(() => scrolling.notReached(place()));

      pinnedBelow();
      expect(pinned()).toHaveClass("sticky", "bottom-(--tab-bar-space)");
    });

    test("pinned while the page is scrolled past their place", async () => {
      const scrolling = watchScrolling();
      await open();
      act(() => liveStream.watching(true));

      act(() => scrolling.scrolledPast(place()));

      pinnedBelow();
    });

    test("in their place while it is in view", async () => {
      const scrolling = watchScrolling();
      await open();
      act(() => liveStream.watching(true));
      act(() => scrolling.notReached(place()));
      pinnedBelow();

      act(() => scrolling.inView(place()));

      inTheirPlace();
    });

    test("pinned while an edge of the screen cuts their place: half a button is no button", async () => {
      const parts = watchParts();
      await open();
      act(() => liveStream.watching(true));

      act(() => parts.shows(place(), 0.5));
      pinnedBelow();

      // All of it, as an engine that rounds says it.
      act(() => parts.shows(place(), 0.995));
      inTheirPlace();

      act(() => parts.shows(place(), 0.9));
      pinnedBelow();

      act(() => parts.shows(place(), 1));
      inTheirPlace();
    });

    test("open the trade sheet from where they are pinned", async () => {
      const scrolling = watchScrolling();
      const { user } = await open();
      act(() => liveStream.watching(true));
      act(() => scrolling.notReached(place()));

      await user.click(within(pinned()!).getByRole("button", { name: "NO 45.2%" }));

      expect(navigation.href).toBe(`${PATH}?buy=NO`);
      expect(
        within(await screen.findByRole("dialog", { name: "Trade" })).getByText("Ticket: BUY NO"),
      ).toBeInTheDocument();
    });

    test("give way to the keyboard while the composer of the chat has the focus", async () => {
      const scrolling = watchScrolling();
      await open();
      act(() => liveStream.watching(true));
      act(() => scrolling.notReached(place()));
      pinnedBelow();

      act(() => liveStream.typing(true));
      inTheirPlace();

      act(() => liveStream.typing(false));
      pinnedBelow();
    });

    test("follow the rule of the screen again after the viewer stops watching", async () => {
      const scrolling = watchScrolling();
      await open();
      act(() => liveStream.watching(true));
      act(() => scrolling.notReached(place()));
      pinnedBelow();

      act(() => liveStream.watching(false));
      act(() => scrolling.notReached(place()));
      inTheirPlace();

      act(() => scrolling.scrolledPast(place()));
      pinnedBelow();
    });

    test("stay pinned when watching begins on a page that is scrolled past them", async () => {
      const scrolling = watchScrolling();
      await open();
      act(() => scrolling.scrolledPast(place()));
      pinnedBelow();

      // Nothing has looked again yet: what was pinned neither goes nor comes back.
      act(() => liveStream.watching(true));
      pinnedBelow();

      act(() => scrolling.scrolledPast(place()));
      pinnedBelow();
    });

    test("the screen takes the room of the tab bar, which is hidden meanwhile: the buttons stand on the lower edge", async () => {
      const frame = () => document.querySelector("main[data-slot=screen]");
      await open();
      expect(frame()).not.toHaveClass(WITHOUT_TAB_BAR);

      act(() => liveStream.watching(true));
      // What keeps clear of the tab bar reads this length: the pinned bar and the chat.
      expect(frame()).toHaveClass(WITHOUT_TAB_BAR);

      act(() => liveStream.watching(false));
      expect(frame()).not.toHaveClass(WITHOUT_TAB_BAR);
    });

    test("one observer looks, at the screen alone, from the lower edge of the video box", async () => {
      layOut({ topBar: 56, box: 257 });
      const scrolling = watchScrolling();
      await open();
      // Nobody watches: from the top bar down, and far past the lower edge of the screen.
      expect(scrolling.margins()).toEqual(["-56px 0px 1000000px 0px"]);

      const box = showBox();
      act(() => liveStream.watching(true));

      expect(scrolling.margins()).toEqual(["-257px 0px 0px 0px"]);

      // The box goes with the watching.
      box.remove();
      act(() => liveStream.watching(false));

      expect(scrolling.margins()).toEqual(["-56px 0px 1000000px 0px"]);
    });

    test("the box is measured again when the window changes its size: a phone that is turned", async () => {
      const bottoms = layOut({ topBar: 56, box: 257 });
      const scrolling = watchScrolling();
      await open();
      showBox();
      act(() => liveStream.watching(true));
      act(() => scrolling.notReached(place()));
      pinnedBelow();

      bottoms.box = 225;
      act(() => {
        window.dispatchEvent(new Event("resize"));
      });

      expect(scrolling.margins()).toEqual(["-225px 0px 0px 0px"]);
      // Until the observer has looked, what it said stands.
      pinnedBelow();
    });
  });

  describe("what does not change", () => {
    test("the composer's focus takes no pinned button away while nobody watches", async () => {
      const scrolling = watchScrolling();
      await open();
      act(() => scrolling.scrolledPast(place()));
      pinnedBelow();

      act(() => liveStream.typing(true));

      pinnedBelow();
    });

    test("nobody watches: a place below the screen pins nothing", async () => {
      const scrolling = watchScrolling();
      await open();

      act(() => scrolling.notReached(place()));

      inTheirPlace();
    });

    test("from 768 px nothing is pinned while the viewer watches", async () => {
      setViewport("wide");
      const scrolling = watchScrolling();
      await open();

      act(() => liveStream.watching(true));
      act(() => scrolling.notReached(place()));
      inTheirPlace();

      act(() => scrolling.scrolledPast(place()));
      inTheirPlace();
      // No tab bar stands at the lower edge at this width, and no room is taken.
      expect(document.querySelector("main[data-slot=screen]")).not.toHaveClass(WITHOUT_TAB_BAR);
      // The screen's own observer: the video box sticks nowhere at this width.
      expect(scrolling.margins()).toHaveLength(1);
      expect(scrolling.margins()[0]).toMatch(/^-\d+px 0px 1000000px 0px$/);
    });

    test("a market that does not trade has no buttons to pin while its viewer watches", async () => {
      const scrolling = watchScrolling();
      await open(closed());

      act(() => liveStream.watching(true));

      expect(document.querySelector("[data-slot=outcome-buttons-place]")).not.toBeInTheDocument();
      expect(pinned()).not.toBeInTheDocument();
      expect(scrolling.margins()).toHaveLength(0);
    });
  });
});
