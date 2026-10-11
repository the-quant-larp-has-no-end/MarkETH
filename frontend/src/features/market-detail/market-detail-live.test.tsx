import { act, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { page } from "@/lib/browser";
import { REDUCED_MOTION_QUERY } from "@/lib/design";
import { sizeCharts } from "@/test/charts";
import { installFakeEventSource } from "@/test/fake-event-source";
import { keys } from "@/lib/api/keys";
import {
  config,
  history,
  holdings,
  MARKET_ID,
  marketDetail,
  marketViewer,
  marketViewerWithPosition,
  me,
  quoteEvent,
  statusEvent,
  TODAY,
} from "@/test/fixtures";
import { json, noAnswer, portal401 } from "@/test/http";
import { setMediaQuery } from "@/test/media";
import { followHistoryWrites, navigation } from "@/test/navigation";
import { renderWithProviders } from "@/test/render";

import { MarketDetailScreen } from "./index";
import { HISTORY, MARKET, ME, mockMarketApi, PATH } from "./testing";

vi.mock("@/features/broadcast", async () => (await import("./testing")).broadcastFeature);
vi.mock("@/features/trade", async () => (await import("./testing")).tradeFeature);

const QUESTION = "Will the Polybahn run without interruption during the exam session?";

type Market = ReturnType<typeof marketDetail>;

/** The screen with its market read and its stream open. */
async function follow(market: Market = marketDetail(), pollSeconds = 10) {
  const api = mockMarketApi(market);
  const streams = installFakeEventSource();
  const view = renderWithProviders(<MarketDetailScreen marketId={MARKET_ID} />, {
    config: config({ live_poll_fallback_seconds: pollSeconds }),
  });
  await screen.findByRole("heading", { level: 1, name: QUESTION });
  if (streams.instances.length > 0) act(() => streams.latest().open());
  return { api, streams, ...view };
}

const closed = (viewer = marketViewerWithPosition({ can_trade: false })) =>
  marketDetail({ status: "CLOSED", trading_open: false, viewer });

/** The market as it is once VIS resolved it YES: the viewer's shares were paid out. */
const resolved = (payout_cr: string) =>
  marketDetail({
    status: "RESOLVED",
    outcome: "YES",
    trading_open: false,
    resolution: {
      outcome: "YES",
      evidence: "The operator reported no interruption.",
      resolved_at: "2026-10-20T09:30:00Z",
    },
    viewer: marketViewerWithPosition({
      can_trade: false,
      payout_cr,
      holdings: holdings({ shares: "91.645", value_cr: payout_cr }),
    }),
  });

const beans = () => document.querySelectorAll("[data-slot=position] .animate-bean-drop");

beforeEach(() => {
  navigation.go(PATH);
  followHistoryWrites();
  sizeCharts();
  // The screen sets a timer for the market's close time: the tests stand on one day.
  vi.useFakeTimers({ now: TODAY, toFake: ["Date"] });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("MarketDetailScreen, live", () => {
  test("follows the event stream of its market, on the page's own origin", async () => {
    const { streams } = await follow();

    expect(streams.instances).toHaveLength(1);
    expect(streams.latest().url).toBe(`/api/markets/${MARKET_ID}/stream`);
  });

  test("opens no stream before the market has been read", () => {
    mockMarketApi();
    const streams = installFakeEventSource();

    renderWithProviders(<MarketDetailScreen marketId={MARKET_ID} />);

    expect(streams.instances).toHaveLength(0);
  });

  test("a quote event updates the price, the volume, the buttons and the chart from the event", async () => {
    const { api, streams } = await follow();
    await screen.findByRole("application");
    const asked = api.requests.length;
    const before = document.querySelector(".recharts-area-curve")!.getAttribute("d");

    act(() =>
      streams
        .latest()
        .emit("quote", quoteEvent({ p_yes: 0.559, p_no: 0.441, volume_cr: "1275.5", version: 8 })),
    );

    expect(await screen.findByText("55.9%")).toBeInTheDocument();
    expect(screen.getByText("1,275.5")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "YES 55.9%" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "NO 44.1%" })).toBeInTheDocument();
    // The chart got the event's point, and its summary the figure it brought.
    expect(
      screen.getByRole("application", {
        name: "YES probability over time, now 55.9 percent, opened at 50.0 percent",
      }),
    ).toBeInTheDocument();
    expect(document.querySelector(".recharts-area-curve")!.getAttribute("d")).not.toBe(before);
    expect(screen.getByText("Opened at 50.0%, now 55.9%.")).toBeInTheDocument();
    // Nobody holds anything here: the event is enough, and the API is not asked.
    expect(api.requests).toHaveLength(asked);
  });

  test("an event whose version is not above the one on screen is ignored", async () => {
    const { streams } = await follow();
    await screen.findByRole("application");
    const before = document.querySelector(".recharts-area-curve")!.getAttribute("d");

    act(() => streams.latest().emit("quote", quoteEvent({ p_yes: 0.2, p_no: 0.8, version: 7 })));
    act(() => streams.latest().emit("quote", quoteEvent({ p_yes: 0.1, p_no: 0.9, version: 3 })));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });

    expect(screen.getByText("54.8%")).toBeInTheDocument();
    expect(screen.queryByText("20.0%")).not.toBeInTheDocument();
    expect(document.querySelector(".recharts-area-curve")!.getAttribute("d")).toBe(before);
  });

  test("the live price is in a polite live region that speaks at most once per ten seconds", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { streams } = await follow();
    const region = document.querySelector("[aria-live=polite][aria-atomic=true]")!;
    expect(region).toHaveTextContent("YES 54.8 percent");
    await act(() => vi.advanceTimersByTimeAsync(100));

    act(() => streams.latest().emit("quote", quoteEvent({ p_yes: 0.559, version: 8 })));
    act(() => streams.latest().emit("quote", quoteEvent({ p_yes: 0.571, version: 9 })));

    // The figure on screen does not wait; what is read out does.
    expect(await screen.findByText("57.1%")).toBeInTheDocument();
    expect(region).toHaveTextContent("YES 54.8 percent");

    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(region).toHaveTextContent("YES 57.1 percent");
  });

  test("a status event reads the market again, and the screen shows its real status", async () => {
    const { api, streams } = await follow();
    api.on(
      MARKET,
      json(
        200,
        marketDetail({
          status: "HALTED",
          trading_open: false,
          viewer: marketViewer({ can_trade: false, cannot_trade_reason: "MARKET_NOT_OPEN" }),
        }),
      ),
    );

    act(() => streams.latest().emit("status", statusEvent({ status: "HALTED" })));

    expect(await screen.findByText("Trading is paused.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "YES 54.8%" })).toBeDisabled();
    expect(screen.getByText("Paused")).toBeInTheDocument();
    expect(api.calls(MARKET)).toHaveLength(2);
  });

  test("while the viewer holds a position, a quote event also reads the market again: its worth is the server's to say", async () => {
    const { api, streams } = await follow(marketDetail({ viewer: marketViewerWithPosition() }));
    const position = screen.getByRole("region", { name: "Your position" });
    expect(within(position).getByText("50.22")).toBeInTheDocument();
    api.on(
      MARKET,
      json(
        200,
        marketDetail({
          p_yes: 0.559,
          p_no: 0.441,
          version: 8,
          viewer: marketViewerWithPosition({
            holdings: holdings({ shares: "91.645", value_cr: "51.229555" }),
          }),
        }),
      ),
    );

    act(() =>
      streams.latest().emit("quote", quoteEvent({ p_yes: 0.559, p_no: 0.441, version: 8 })),
    );

    expect(await within(position).findByText("51.22")).toBeInTheDocument();
    expect(screen.getByText("55.9%")).toBeInTheDocument();
    expect(api.calls(MARKET)).toHaveLength(2);
  });

  test.each([
    ["RESOLVED", "YES"],
    ["VOID", "VOID"],
  ] as const)("a %s market has nothing left to follow: no stream", async (status, outcome) => {
    const { streams } = await follow(
      marketDetail({
        status,
        outcome,
        trading_open: false,
        resolution: { outcome, evidence: "Decided.", resolved_at: "2026-10-20T09:30:00Z" },
        viewer: marketViewer({ can_trade: false, cannot_trade_reason: "MARKET_NOT_OPEN" }),
      }),
    );

    expect(streams.instances).toHaveLength(0);
  });

  test("a closed market is still followed: its resolution is yet to come", async () => {
    const { streams } = await follow(closed());

    expect(streams.instances).toHaveLength(1);
  });

  test("closes the stream when the screen goes away", async () => {
    const { streams, unmount } = await follow();
    const stream = streams.latest();

    unmount();

    expect(stream.closed).toBe(true);
  });

  describe("when the stream fails", () => {
    beforeEach(() => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
    });

    test("reads the market and its history at once, then every live_poll_fallback_seconds, and shows no error", async () => {
      const { api, streams } = await follow(marketDetail(), 4);
      await waitFor(() => expect(api.calls(HISTORY)).toHaveLength(1));
      api.on(MARKET, json(200, marketDetail({ p_yes: 0.6, p_no: 0.4, version: 12 })));

      act(() => streams.latest().fail());

      // At once: the screen keeps working, a little slower.
      expect(await screen.findByText("60.0%")).toBeInTheDocument();
      expect(api.calls(MARKET)).toHaveLength(2);
      await waitFor(() => expect(api.calls(HISTORY)).toHaveLength(2));
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();

      // Then with the interval of /config, trying the stream again at each poll.
      await act(() => vi.advanceTimersByTimeAsync(3_900));
      expect(api.calls(MARKET)).toHaveLength(2);
      await act(() => vi.advanceTimersByTimeAsync(200));
      await waitFor(() => expect(api.calls(MARKET)).toHaveLength(3));
      expect(streams.instances).toHaveLength(2);
    });

    test("stops polling once the stream is back", async () => {
      const { api, streams } = await follow(marketDetail(), 4);
      act(() => streams.latest().fail());
      await waitFor(() => expect(api.calls(MARKET)).toHaveLength(2));
      await act(() => vi.advanceTimersByTimeAsync(4_000));
      await waitFor(() => expect(api.calls(MARKET)).toHaveLength(3));

      act(() => streams.latest().open());
      await act(() => vi.advanceTimersByTimeAsync(20_000));

      expect(api.calls(MARKET)).toHaveLength(3);
    });

    test("a poll without a connection leaves the market on screen, without an error banner", async () => {
      const { api, streams } = await follow(marketDetail(), 4);
      api.on(MARKET, noAnswer);
      api.on(HISTORY, noAnswer);

      act(() => streams.latest().fail());

      await waitFor(() => expect(api.calls(MARKET)).toHaveLength(2));
      expect(screen.getByRole("heading", { level: 1, name: QUESTION })).toBeInTheDocument();
      expect(screen.getByText("54.8%")).toBeInTheDocument();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    test("tells an ended portal session from a failed stream: the first poll is a JSON call, and the portal's 401 reloads the page", async () => {
      const reload = vi.spyOn(page, "reload").mockImplementation(() => {});
      const { api, streams } = await follow(marketDetail(), 4);
      api.on(MARKET, portal401());
      api.on(HISTORY, portal401());

      act(() => streams.latest().fail());

      await waitFor(() => expect(reload).toHaveBeenCalledTimes(1));
      // The probe asked for JSON, which is what makes the portal answer 401 and not redirect.
      expect(api.lastCall(MARKET)?.headers.get("Accept")).toBe("application/json");
    });

    test("a failed stream alone reloads nothing", async () => {
      const reload = vi.spyOn(page, "reload").mockImplementation(() => {});
      const { api, streams } = await follow(marketDetail(), 4);

      act(() => streams.latest().fail());
      await waitFor(() => expect(api.calls(MARKET)).toHaveLength(2));
      await act(() => vi.advanceTimersByTimeAsync(4_000));

      expect(reload).not.toHaveBeenCalled();
    });
  });

  describe("when the market's close time passes", () => {
    /** Twenty seconds before the market of the fixtures closes, at 16:00 UTC. */
    const BEFORE_CLOSE = new Date("2026-10-14T15:59:40Z");

    /** The market as the API answers it once its close time has passed. */
    const afterClose = () =>
      marketDetail({
        status: "CLOSED",
        trading_open: false,
        viewer: marketViewer({
          can_trade: false,
          cannot_trade_reason: "MARKET_NOT_OPEN",
          buy_slider: null,
        }),
      });

    beforeEach(() => {
      vi.useFakeTimers({ now: BEFORE_CLOSE, shouldAdvanceTime: true });
    });

    test("asks for the market and its chart again at that moment, and shows what the answer says", async () => {
      const { api } = await follow();
      await waitFor(() => expect(api.calls(HISTORY)).toHaveLength(1));
      expect(screen.getByRole("button", { name: "YES 54.8%" })).toBeEnabled();
      expect(screen.getByText("Closes today, 18:00")).toBeInTheDocument();
      api.on(MARKET, json(200, afterClose()));
      api.on(HISTORY, json(200, history({ until: "2026-10-14T16:00:00Z" })));

      // Not before its time: no event and no clock makes the screen ask.
      await act(() => vi.advanceTimersByTimeAsync(15_000));
      expect(api.calls(MARKET)).toHaveLength(1);
      expect(api.calls(HISTORY)).toHaveLength(1);

      await act(() => vi.advanceTimersByTimeAsync(5_500));

      // The badge, the banner, the buttons and the deadline all follow the answer.
      expect(await screen.findByText(/^Trading is over\./)).toBeInTheDocument();
      expect(screen.getByText("Closed")).toHaveAttribute("data-slot", "badge");
      expect(screen.queryByRole("button", { name: /^(YES|NO) / })).not.toBeInTheDocument();
      expect(document.querySelector("[data-slot=deadline]")).toHaveTextContent(/^14 Oct 2026$/);
      expect(screen.queryByText("Closes today, 18:00")).not.toBeInTheDocument();
      expect(api.calls(MARKET)).toHaveLength(2);
      await waitFor(() => expect(api.calls(HISTORY)).toHaveLength(2));
      // The chart is read again with the market: it says "closed at", and ends where the
      // server says trading ended.
      expect(await screen.findByText("Opened at 50.0%, closed at 54.8%.")).toBeInTheDocument();
      const dates = [...document.querySelectorAll(".recharts-xAxis-tick-labels text")];
      expect(dates.at(-1)).toHaveTextContent("14 Oct 2026");

      // With the answer the wait is over: nothing is asked again.
      await act(() => vi.advanceTimersByTimeAsync(60_000));
      expect(api.calls(MARKET)).toHaveLength(2);
      expect(api.calls(HISTORY)).toHaveLength(2);
    });

    test("the timer decides nothing: while the API still says open, the screen shows it open and asks again every live_poll_fallback_seconds", async () => {
      // The clock of the device runs ahead of the server's.
      const { api } = await follow(marketDetail(), 4);
      await waitFor(() => expect(api.calls(HISTORY)).toHaveLength(1));

      await act(() => vi.advanceTimersByTimeAsync(20_500));
      await waitFor(() => expect(api.calls(MARKET)).toHaveLength(2));

      // By the device the close time has passed. What is shown is what the server said.
      expect(screen.getByText("Open")).toHaveAttribute("data-slot", "badge");
      expect(screen.getByRole("button", { name: "YES 54.8%" })).toBeEnabled();
      expect(screen.getByRole("button", { name: "NO 45.2%" })).toBeEnabled();
      expect(screen.queryByText(/^Trading is over\./)).not.toBeInTheDocument();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();

      await act(() => vi.advanceTimersByTimeAsync(4_000));
      await waitFor(() => expect(api.calls(MARKET)).toHaveLength(3));

      api.on(MARKET, json(200, afterClose()));
      await act(() => vi.advanceTimersByTimeAsync(4_000));

      expect(await screen.findByText(/^Trading is over\./)).toBeInTheDocument();
      const asked = api.calls(MARKET).length;
      await act(() => vi.advanceTimersByTimeAsync(20_000));
      expect(api.calls(MARKET)).toHaveLength(asked);
    });

    test("an answer that does not arrive is asked for again at the same pace, and shows no error", async () => {
      const { api } = await follow(marketDetail(), 4);
      await waitFor(() => expect(api.calls(HISTORY)).toHaveLength(1));
      api.on(MARKET, noAnswer);
      api.on(HISTORY, noAnswer);

      await act(() => vi.advanceTimersByTimeAsync(20_500));
      await waitFor(() => expect(api.calls(MARKET)).toHaveLength(2));
      expect(screen.getByRole("heading", { level: 1, name: QUESTION })).toBeInTheDocument();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();

      api.on(MARKET, json(200, afterClose()));
      api.on(HISTORY, json(200, history({ until: "2026-10-14T16:00:00Z" })));
      await act(() => vi.advanceTimersByTimeAsync(4_000));

      expect(await screen.findByText(/^Trading is over\./)).toBeInTheDocument();
    });

    test("a market that is not shown as open for trading waits for nothing", async () => {
      const { api } = await follow(
        marketDetail({
          status: "HALTED",
          trading_open: false,
          viewer: marketViewer({ can_trade: false, cannot_trade_reason: "MARKET_NOT_OPEN" }),
        }),
      );
      await waitFor(() => expect(api.calls(HISTORY)).toHaveLength(1));

      await act(() => vi.advanceTimersByTimeAsync(60_000));

      expect(api.calls(MARKET)).toHaveLength(1);
      expect(api.calls(HISTORY)).toHaveLength(1);
    });

    /** A proposal, as its proposer and admins are shown it. Nobody trades it. */
    const proposal = (status: "PROPOSED" | "REJECTED" | "EXPIRED") =>
      marketDetail({
        status,
        trading_open: false,
        opened_at: null,
        rejection_reason: status === "REJECTED" ? "The source is not public." : null,
        viewer: marketViewer({
          can_trade: false,
          cannot_trade_reason: "MARKET_NOT_OPEN",
          buy_slider: null,
        }),
      });

    test("a proposal in review is asked for again at its close time, and shows that it expired", async () => {
      const { api, streams } = await follow(proposal("PROPOSED"));
      expect(screen.getByText("In review")).toHaveAttribute("data-slot", "badge");
      api.on(MARKET, json(200, proposal("EXPIRED")));

      // Not before its time.
      await act(() => vi.advanceTimersByTimeAsync(15_000));
      expect(api.calls(MARKET)).toHaveLength(1);

      await act(() => vi.advanceTimersByTimeAsync(5_500));

      expect(await screen.findByText("Expired")).toHaveAttribute("data-slot", "badge");
      expect(screen.queryByText("In review")).not.toBeInTheDocument();
      expect(api.calls(MARKET)).toHaveLength(2);
      // A proposal has no chart and no stream, at its close time as before it.
      expect(api.calls(HISTORY)).toHaveLength(0);
      expect(streams.instances).toHaveLength(0);

      // With the answer the wait is over: nothing is asked again.
      await act(() => vi.advanceTimersByTimeAsync(60_000));
      expect(api.calls(MARKET)).toHaveLength(2);
    });

    test("the timer does not expire a proposal: while the API still says in review, the screen says so and asks again", async () => {
      // The clock of the device runs ahead of the server's.
      const { api } = await follow(proposal("PROPOSED"), 4);

      await act(() => vi.advanceTimersByTimeAsync(20_500));
      await waitFor(() => expect(api.calls(MARKET)).toHaveLength(2));
      expect(screen.getByText("In review")).toHaveAttribute("data-slot", "badge");
      expect(screen.queryByText("Expired")).not.toBeInTheDocument();

      api.on(MARKET, json(200, proposal("EXPIRED")));
      await act(() => vi.advanceTimersByTimeAsync(4_000));

      expect(await screen.findByText("Expired")).toHaveAttribute("data-slot", "badge");
      const asked = api.calls(MARKET).length;
      await act(() => vi.advanceTimersByTimeAsync(20_000));
      expect(api.calls(MARKET)).toHaveLength(asked);
    });

    test.each(["REJECTED", "EXPIRED"] as const)(
      "a proposal that is %s waits for nothing: its close time ends nothing",
      async (status) => {
        const { api } = await follow(proposal(status));

        await act(() => vi.advanceTimersByTimeAsync(60_000));

        expect(api.calls(MARKET)).toHaveLength(1);
      },
    );

    test("is set again when the close time is another one", async () => {
      const { api, client } = await follow();
      await waitFor(() => expect(api.calls(HISTORY)).toHaveLength(1));

      // The market was read again and closes two minutes later than it said before.
      act(() =>
        client.setQueryData(
          keys.markets.detail(MARKET_ID),
          marketDetail({ close_at: "2026-10-14T16:02:00Z" }),
        ),
      );
      await act(() => vi.advanceTimersByTimeAsync(60_000));
      expect(api.calls(MARKET)).toHaveLength(1);

      api.on(MARKET, json(200, afterClose()));
      await act(() => vi.advanceTimersByTimeAsync(81_000));

      await waitFor(() => expect(api.calls(MARKET)).toHaveLength(2));
      expect(await screen.findByText(/^Trading is over\./)).toBeInTheDocument();
    });

    test("a status event that ends trading before the close time ends the wait", async () => {
      const { api, streams } = await follow();
      await waitFor(() => expect(api.calls(HISTORY)).toHaveLength(1));
      api.on(
        MARKET,
        json(
          200,
          marketDetail({
            status: "HALTED",
            trading_open: false,
            viewer: marketViewer({ can_trade: false, cannot_trade_reason: "MARKET_NOT_OPEN" }),
          }),
        ),
      );

      act(() => streams.latest().emit("status", statusEvent({ status: "HALTED" })));
      expect(await screen.findByText("Trading is paused.")).toBeInTheDocument();
      const asked = api.calls(MARKET).length;

      await act(() => vi.advanceTimersByTimeAsync(60_000));

      expect(api.calls(MARKET)).toHaveLength(asked);
    });

    test("is cleared when the screen goes away", async () => {
      const { api, unmount } = await follow();
      await waitFor(() => expect(api.calls(HISTORY)).toHaveLength(1));

      unmount();
      await act(() => vi.advanceTimersByTimeAsync(60_000));

      expect(api.calls(MARKET)).toHaveLength(1);
    });

    test("days before the close time nothing is asked", async () => {
      vi.setSystemTime(TODAY);
      const { api } = await follow();
      await waitFor(() => expect(api.calls(HISTORY)).toHaveLength(1));

      await act(() => vi.advanceTimersByTimeAsync(10 * 60_000));

      expect(api.calls(MARKET)).toHaveLength(1);
      expect(api.calls(HISTORY)).toHaveLength(1);
    });
  });

  describe("a payout that arrives while the market is on screen", () => {
    beforeEach(() => {
      // Let the beans fall: a test starts with reduced motion.
      setMediaQuery(REDUCED_MOTION_QUERY, false);
    });

    test("drops the beans into Paid out when a status event brought the resolution", async () => {
      const { api, streams } = await follow(closed());
      expect(beans()).toHaveLength(0);
      api.on(MARKET, json(200, resolved("91.645")));
      api.on(ME, json(200, me({ balance_cr: "331.645" })));

      act(() =>
        streams.latest().emit("status", statusEvent({ status: "RESOLVED", trading_open: false })),
      );

      const position = screen.getByRole("region", { name: "Your position" });
      expect(await within(position).findByText("Paid out")).toBeInTheDocument();
      expect(beans().length).toBeGreaterThanOrEqual(3);
      expect(beans().length).toBeLessThanOrEqual(5);
      expect(within(position).getByText("+91.64 tokens")).toBeInTheDocument();
      // Once, and then they are gone.
      await waitFor(() => expect(beans()).toHaveLength(0), { timeout: 2000 });
    });

    test("a resolution reads the balance again: the top bar shows what the payout made of it", async () => {
      const { api, streams } = await follow(closed());
      expect(screen.getByRole("link", { name: "Wallet, 240 tokens" })).toBeInTheDocument();
      api.on(MARKET, json(200, resolved("91.645")));
      api.on(ME, json(200, me({ balance_cr: "331.645" })));

      act(() =>
        streams.latest().emit("status", statusEvent({ status: "RESOLVED", trading_open: false })),
      );

      expect(
        await screen.findByRole("link", { name: "Wallet, 331.64 tokens" }),
      ).toBeInTheDocument();
      expect(api.calls(ME)).toHaveLength(1);
    });

    test("a void reads the balance again too: the stake was refunded", async () => {
      const { api, streams } = await follow(closed());
      api.on(
        MARKET,
        json(
          200,
          marketDetail({
            status: "VOID",
            outcome: "VOID",
            trading_open: false,
            viewer: marketViewerWithPosition({ can_trade: false, refund_cr: "48" }),
          }),
        ),
      );
      api.on(ME, json(200, me({ balance_cr: "288" })));

      act(() =>
        streams.latest().emit("status", statusEvent({ status: "VOID", trading_open: false })),
      );

      expect(await screen.findByRole("link", { name: "Wallet, 288 tokens" })).toBeInTheDocument();
    });

    test("stops following the market once it is resolved", async () => {
      const { api, streams } = await follow(closed());
      const stream = streams.latest();
      api.on(MARKET, json(200, resolved("91.645")));
      api.on(ME, json(200, me()));

      act(() => stream.emit("status", statusEvent({ status: "RESOLVED", trading_open: false })));

      await screen.findByText("Paid out");
      expect(stream.closed).toBe(true);
    });

    test("drops nothing when the market paid the viewer nothing", async () => {
      const { api, streams } = await follow(closed());
      api.on(MARKET, json(200, resolved("0")));
      api.on(ME, json(200, me()));

      act(() =>
        streams.latest().emit("status", statusEvent({ status: "RESOLVED", trading_open: false })),
      );

      await screen.findByText("Paid out");
      expect(beans()).toHaveLength(0);
    });

    test("drops nothing on page load: a market that was resolved while nobody looked simply shows", async () => {
      await follow(resolved("91.645"));

      expect(screen.getByText("Paid out")).toBeInTheDocument();
      expect(beans()).toHaveLength(0);
    });

    test("drops nothing when the resolution was only found by reading again, without an event", async () => {
      const { api } = await follow(closed());
      api.on(MARKET, json(200, resolved("91.645")));
      api.on("GET /api/me", noAnswer);

      act(() => {
        window.dispatchEvent(new Event("visibilitychange"));
      });

      await screen.findByText("Paid out");
      expect(beans()).toHaveLength(0);
    });

    test("drops nothing for another status event", async () => {
      const { api, streams } = await follow(marketDetail({ viewer: marketViewerWithPosition() }));
      api.on(MARKET, json(200, closed()));

      act(() =>
        streams.latest().emit("status", statusEvent({ status: "CLOSED", trading_open: false })),
      );

      await screen.findByText(/Trading is over/);
      expect(beans()).toHaveLength(0);
    });

    test("with reduced motion nothing falls and the figure is simply there", async () => {
      setMediaQuery(REDUCED_MOTION_QUERY, true);
      const { api, streams } = await follow(closed());
      api.on(MARKET, json(200, resolved("91.645")));
      api.on(ME, json(200, me()));

      act(() =>
        streams.latest().emit("status", statusEvent({ status: "RESOLVED", trading_open: false })),
      );

      expect(await screen.findByText("+91.64 tokens")).toBeInTheDocument();
      expect(beans()).toHaveLength(0);
    });
  });
});
