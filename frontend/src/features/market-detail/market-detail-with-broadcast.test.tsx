import { act, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { AppShell } from "@/components/shell";
import { sizeCharts } from "@/test/charts";
import {
  broadcastIn,
  broadcastViewer,
  chat,
  history,
  MARKET_ID,
  marketBroadcast,
  marketDetail,
  noBroadcast,
  TODAY,
} from "@/test/fixtures";
import { json, mockApi, type Route } from "@/test/http";
import { setViewport } from "@/test/media";
import { followHistoryWrites, navigation } from "@/test/navigation";
import { watchScrolling } from "@/test/observers";
import { renderWithProviders } from "@/test/render";

import { MarketDetailScreen } from "./index";
import { HISTORY, MARKET, PATH, resetTrade } from "./testing";

vi.mock("@/features/trade", async () => (await import("./testing")).tradeFeature);

/**
 * The live stream of `@/features/broadcast` and the live chat of `@/features/chat` inside
 * this screen: neither is a stand-in. The other tests of the screen put a stand-in in the
 * place of the section; this is the check that they fit together, from "Watch live" to the
 * outcome buttons at the lower edge of a phone. jsdom plays no video: the box is there and
 * says so, which is all this file needs of it.
 */

const BROADCAST: Route = "GET /api/markets/{market_id}/broadcast";
const CHAT: Route = "GET /api/markets/{market_id}/chat";

const QUESTION = "Will the Polybahn run without interruption during the exam session?";
const STUDIO = `/markets/${MARKET_ID}/live`;

function open(broadcast = marketBroadcast(), market = marketDetail()) {
  const api = mockApi({
    [MARKET]: json(200, market),
    [HISTORY]: json(200, history()),
    [BROADCAST]: json(200, broadcast),
    [CHAT]: json(200, chat()),
  });
  const view = renderWithProviders(
    <AppShell>
      <MarketDetailScreen marketId={MARKET_ID} />
    </AppShell>,
  );
  return { api, ...view };
}

const loaded = () => screen.findByRole("heading", { level: 1, name: QUESTION });
const card = () => screen.findByRole("region", { name: "Live stream" });
const box = () => document.querySelector<HTMLElement>("[data-slot=live-stream-box]");
const place = () => document.querySelector<HTMLElement>("[data-slot=outcome-buttons-place]")!;
const pinned = () => document.querySelector<HTMLElement>("[data-slot=pinned]");
const tabBar = () => screen.queryByRole("navigation", { name: "Main" });

/** `first` stands before `second` on the page. */
const before = (first: Element, second: Element) =>
  (first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;

beforeEach(() => {
  navigation.go(PATH);
  followHistoryWrites();
  sizeCharts();
  resetTrade();
  vi.useFakeTimers({ now: TODAY, toFake: ["Date"] });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("MarketDetailScreen with the live stream and its chat", () => {
  test("a live stream that is on stands between the Price and the chart, with the live chat under it", async () => {
    open();

    const section = await card();
    expect(within(section).getByText("A live stream is on for this market.")).toBeInTheDocument();
    expect(within(section).getByRole("button", { name: "Watch live" })).toBeEnabled();

    const liveChat = await screen.findByRole("region", { name: "Live chat" });
    const price = document.querySelector("[data-slot=price]")!;
    const chart = await screen.findByRole("figure");
    expect(before(price, section)).toBe(true);
    expect(before(section, liveChat)).toBe(true);
    expect(before(liveChat, chart)).toBe(true);
    expect(before(chart, place())).toBe(true);

    // Nothing plays, nothing is pinned and the tab bar is there before the tap.
    expect(box()).not.toBeInTheDocument();
    expect(pinned()).not.toBeInTheDocument();
    expect(tabBar()).toBeInTheDocument();
  });

  test("watching on a phone: the box heads the screen's column, the tab bar goes, and the outcome buttons are pinned while their place is out of view", async () => {
    const scrolling = watchScrolling();
    const { user } = open();

    await user.click(within(await card()).getByRole("button", { name: "Watch live" }));

    // The box the screen measures against: it sticks in the column of the screen, above the question.
    await waitFor(() => expect(box()).toBeInTheDocument());
    const column = (await loaded()).closest("header")!.parentElement!;
    expect(box()!.parentElement).toBe(column);
    expect(box()).toHaveClass("sticky", "order-first");
    await waitFor(() => expect(tabBar()).not.toBeInTheDocument());

    act(() => scrolling.notReached(place()));
    expect(within(pinned()!).getByRole("button", { name: "YES 54.8%" })).toBeEnabled();
    expect(within(pinned()!).getByRole("button", { name: "NO 45.2%" })).toBeEnabled();
    expect(place()).toBeEmptyDOMElement();

    act(() => scrolling.inView(place()));
    expect(pinned()).not.toBeInTheDocument();
    expect(within(place()).getByRole("button", { name: "YES 54.8%" })).toBeInTheDocument();

    act(() => scrolling.scrolledPast(place()));
    expect(within(pinned()!).getByRole("button", { name: "YES 54.8%" })).toBeEnabled();
  });

  test("the composer of the chat takes the pinned buttons away while it has the focus", async () => {
    const scrolling = watchScrolling();
    const { user } = open();
    await user.click(within(await card()).getByRole("button", { name: "Watch live" }));
    await waitFor(() => expect(box()).toBeInTheDocument());
    act(() => scrolling.notReached(place()));
    expect(pinned()).toBeInTheDocument();

    await user.click(await screen.findByRole("textbox", { name: "Message" }));

    await waitFor(() => expect(pinned()).not.toBeInTheDocument());
    expect(within(place()).getByRole("button", { name: "YES 54.8%" })).toBeInTheDocument();

    await user.tab();

    await waitFor(() => expect(pinned()).toBeInTheDocument());
  });

  test('"Stop watching" gives the tab bar back, and a place below the screen pins nothing again', async () => {
    const scrolling = watchScrolling();
    const { user } = open();
    await user.click(within(await card()).getByRole("button", { name: "Watch live" }));
    await waitFor(() => expect(tabBar()).not.toBeInTheDocument());
    act(() => scrolling.notReached(place()));
    expect(pinned()).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Stop watching" }));

    await waitFor(() => expect(tabBar()).toBeInTheDocument());
    expect(box()).not.toBeInTheDocument();
    act(() => scrolling.notReached(place()));
    expect(pinned()).not.toBeInTheDocument();
    expect(within(place()).getByRole("button", { name: "NO 45.2%" })).toBeInTheDocument();
  });

  test("from 768 px the box stands in the card, the tab bar is none of this screen's, and nothing is pinned", async () => {
    setViewport("wide");
    const scrolling = watchScrolling();
    const { user } = open();

    await user.click(within(await card()).getByRole("button", { name: "Watch live" }));

    expect(await screen.findByRole("button", { name: "Stop watching" })).toBeInTheDocument();
    expect(box()).not.toBeInTheDocument();
    act(() => scrolling.notReached(place()));
    expect(pinned()).not.toBeInTheDocument();
  });

  describe("the way to the studio is the section's, by the flags of the answer", () => {
    test("the proposer whose live stream is approved", async () => {
      open(
        marketBroadcast({
          broadcast: broadcastIn("APPROVED"),
          viewer: broadcastViewer({ is_broadcaster: true, can_start: true, can_watch: false }),
        }),
      );

      const link = within(await card()).getByRole("link", { name: "Set up the live stream" });
      expect(link).toHaveAttribute("href", STUDIO);
    });

    test("whoever is on air, an admin of VIS as well", async () => {
      open(marketBroadcast({ viewer: broadcastViewer({ is_broadcaster: true, can_end: true }) }));

      const link = within(await card()).getByRole("link", { name: "Open the studio" });
      expect(link).toHaveAttribute("href", STUDIO);
    });
  });

  test("a market without a live stream shows nothing of one, and is asked about it once", async () => {
    const { api } = open(noBroadcast());
    await loaded();

    await waitFor(() => expect(api.calls(BROADCAST)).toHaveLength(1));
    expect(screen.queryByRole("region", { name: "Live stream" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Live chat" })).not.toBeInTheDocument();
    expect(api.calls(CHAT)).toHaveLength(0);
  });

  test("a proposal is asked about no live stream", async () => {
    // No route of the live stream: a request for one fails the test.
    mockApi({ [MARKET]: json(200, marketDetail({ status: "PROPOSED", trading_open: false })) });
    renderWithProviders(<MarketDetailScreen marketId={MARKET_ID} />);

    await loaded();

    expect(screen.getByText("In review")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Live stream" })).not.toBeInTheDocument();
  });
});
