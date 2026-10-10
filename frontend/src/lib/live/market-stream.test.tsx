import { useQuery } from "@tanstack/react-query";
import { act, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { keys } from "@/lib/api/keys";
import { marketHistoryQuery, marketQuery } from "@/lib/api/queries";
import type { HistoryResponse, MarketDetailResponse, StatusEvent } from "@/lib/api/schema";
import { installFakeEventSource } from "@/test/fake-event-source";
import {
  config,
  history,
  MARKET_ID,
  marketDetail,
  marketViewerWithPosition,
  quote,
  quoteEvent,
  statusEvent,
} from "@/test/fixtures";
import { deferred, json, mockApi, type Route } from "@/test/http";
import { renderWithProviders } from "@/test/render";

import { useMarketStream } from "./market-stream";

const MARKET: Route = "GET /api/markets/{market_id}";
const HISTORY: Route = "GET /api/markets/{market_id}/history";

/** A market screen in miniature: it reads the two queries the stream keeps current. */
function Market({
  enabled = true,
  onStatus,
}: {
  enabled?: boolean;
  onStatus?: (event: StatusEvent) => void;
}) {
  const market = useQuery(marketQuery(MARKET_ID));
  const chart = useQuery(marketHistoryQuery(MARKET_ID));
  const { live } = useMarketStream(MARKET_ID, { enabled, onStatus });
  if (market.data === undefined || chart.data === undefined) return <p>loading</p>;
  return (
    <p>
      {live ? "live" : "not live"} | p_yes {market.data.p_yes} | volume {market.data.volume_cr} |
      version {market.data.version} | value {market.data.viewer.holdings[0]?.value_cr} | status{" "}
      {market.data.status} | points {chart.data.points.length}
    </p>
  );
}

const shown = () => screen.getByText(/p_yes/).textContent;

async function open(
  detail = marketDetail(),
  options: { pollSeconds?: number; onStatus?: (event: StatusEvent) => void } = {},
) {
  const api = mockApi({ [MARKET]: json(200, detail), [HISTORY]: json(200, history()) });
  const streams = installFakeEventSource();
  const view = renderWithProviders(<Market onStatus={options.onStatus} />, {
    config: config({ live_poll_fallback_seconds: options.pollSeconds ?? 10 }),
  });
  await screen.findByText(/p_yes 0\.548/);
  act(() => streams.latest().open());
  return { api, streams, ...view };
}

describe("useMarketStream", () => {
  test("follows the stream of its market", async () => {
    const { streams } = await open();

    expect(streams.latest().url).toBe(`/api/markets/${MARKET_ID}/stream`);
    expect(shown()).toContain("live");
  });

  test("a newer quote event updates the price, the volume and the chart from the event itself", async () => {
    const { api, streams } = await open();
    const asked = api.requests.length;

    act(() =>
      streams.latest().emit("quote", quoteEvent({ p_yes: 0.559, volume_cr: "1275.5", version: 8 })),
    );

    await waitFor(() => expect(shown()).toContain("p_yes 0.559"));
    expect(shown()).toContain("volume 1275.5");
    expect(shown()).toContain("version 8");
    expect(shown()).toContain("points 4");
    // The viewer holds nothing here: the event is enough and the API is not asked.
    expect(api.requests).toHaveLength(asked);
  });

  test("an event that is not newer than what is on screen is dropped", async () => {
    const { streams, client } = await open();

    act(() => streams.latest().emit("quote", quoteEvent({ p_yes: 0.2, version: 7 })));
    act(() => streams.latest().emit("quote", quoteEvent({ p_yes: 0.1, version: 3 })));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });

    expect(shown()).toContain("p_yes 0.548");
    expect(shown()).toContain("points 3");
    expect(
      client.getQueryData<HistoryResponse>(keys.markets.history(MARKET_ID))?.points,
    ).toHaveLength(3);
  });

  test("the state the stream sends on connecting changes nothing", async () => {
    const { streams, api } = await open(marketDetail({ viewer: marketViewerWithPosition() }));
    const asked = api.requests.length;

    act(() => streams.latest().emit("quote", quoteEvent({ p_yes: 0.548, version: 7 })));

    expect(shown()).toContain("points 3");
    expect(api.requests).toHaveLength(asked);
  });

  test("an event of another market is not this screen's", async () => {
    const { streams } = await open();

    act(() =>
      streams.latest().emit("quote", quoteEvent({ market_id: "another", p_yes: 0.9, version: 99 })),
    );

    expect(shown()).toContain("p_yes 0.548");
  });

  test("while the viewer has bought in, a quote event also asks for the market: what a holding is worth is the server's to say", async () => {
    const { api, streams } = await open(marketDetail({ viewer: marketViewerWithPosition() }));
    expect(shown()).toContain("value 50.221461");
    api.on(
      MARKET,
      json(
        200,
        marketDetail({
          p_yes: 0.559,
          version: 8,
          volume_cr: "1275.5",
          viewer: marketViewerWithPosition({
            holdings: [
              { outcome: "YES", shares: "91.645", value_cr: "51.229555" },
              { outcome: "NO", shares: "0", value_cr: "0" },
            ],
          }),
        }),
      ),
    );

    act(() =>
      streams.latest().emit("quote", quoteEvent({ p_yes: 0.559, volume_cr: "1275.5", version: 8 })),
    );

    // The price comes from the event; the value follows from the API.
    await waitFor(() => expect(shown()).toContain("p_yes 0.559"));
    await waitFor(() => expect(shown()).toContain("value 51.229555"));
    expect(api.calls(MARKET)).toHaveLength(2);
  });

  test("an answer that is older than an event already shown does not move the price back", async () => {
    const { api, streams } = await open(marketDetail({ viewer: marketViewerWithPosition() }));
    const slow = deferred();
    api.on(MARKET, slow.handler);

    act(() => streams.latest().emit("quote", quoteEvent({ p_yes: 0.559, version: 8 })));
    // A second trade lands while the market is being read.
    act(() => streams.latest().emit("quote", quoteEvent({ p_yes: 0.571, version: 9 })));
    await waitFor(() => expect(shown()).toContain("p_yes 0.571"));

    // The answer still describes version 8.
    api.on(
      MARKET,
      json(200, marketDetail({ p_yes: 0.571, version: 9, viewer: marketViewerWithPosition() })),
    );
    await act(async () => {
      slow.resolve(
        json(200, marketDetail({ p_yes: 0.559, version: 8, viewer: marketViewerWithPosition() })),
      );
    });

    await waitFor(() => expect(api.calls(MARKET).length).toBeGreaterThanOrEqual(3));
    expect(shown()).toContain("p_yes 0.571");
    expect(shown()).toContain("version 9");
  });

  test("a status event asks for the market again", async () => {
    const { api, streams } = await open();
    api.on(MARKET, json(200, marketDetail({ status: "HALTED", trading_open: false })));

    act(() => streams.latest().emit("status", statusEvent({ status: "HALTED" })));

    await waitFor(() => expect(shown()).toContain("status HALTED"));
    expect(api.calls(MARKET)).toHaveLength(2);
  });

  test("a status event is handed to the screen, after the market was asked for again", async () => {
    const seen: { status: string; asking: number }[] = [];
    const view = await open(marketDetail(), {
      onStatus: (event) =>
        seen.push({
          status: event.status,
          asking: view.client.isFetching({ queryKey: keys.markets.detail(MARKET_ID) }),
        }),
    });

    act(() => view.streams.latest().emit("status", statusEvent({ status: "CLOSED" })));
    act(() =>
      view.streams
        .latest()
        .emit("status", statusEvent({ market_id: "another", status: "RESOLVED" })),
    );

    expect(seen).toEqual([{ status: "CLOSED", asking: 1 }]);
    await waitFor(() => expect(view.api.calls(MARKET)).toHaveLength(2));
  });

  test.each(["RESOLVED", "VOID"] as const)(
    "a status event that says the market is %s marks the caller's own reads out of date: the balance is another now",
    async (status) => {
      const { api, streams, client } = await open();
      client.setQueryData(keys.positions, { items: [], total_value_cr: "0" });
      api.on(MARKET, json(200, marketDetail({ status, trading_open: false })));

      act(() => streams.latest().emit("status", statusEvent({ status, trading_open: false })));

      await waitFor(() => expect(shown()).toContain(`status ${status}`));
      expect(client.getQueryState(keys.me)?.isInvalidated).toBe(true);
      expect(client.getQueryState(keys.positions)?.isInvalidated).toBe(true);
    },
  );

  test("a status event that settles nothing leaves the balance alone", async () => {
    const { api, streams, client } = await open();
    api.on(MARKET, json(200, marketDetail({ status: "HALTED", trading_open: false })));

    act(() => streams.latest().emit("status", statusEvent({ status: "HALTED" })));

    await waitFor(() => expect(shown()).toContain("status HALTED"));
    expect(client.getQueryState(keys.me)?.isInvalidated).toBe(false);
  });

  test("an open quote of the trade sheet is never touched by an event", async () => {
    const { streams, client } = await open();
    const request = { side: "BUY", outcome: "YES", amount_cr: "25" } as const;
    const confirmed = quote({ quote_version: 7 });
    client.setQueryData(keys.markets.quote(MARKET_ID, request), confirmed);

    act(() => streams.latest().emit("quote", quoteEvent({ version: 8 })));
    act(() => streams.latest().emit("status", statusEvent()));

    const state = client.getQueryState(keys.markets.quote(MARKET_ID, request));
    expect(state?.data).toEqual(confirmed);
    expect(state?.isInvalidated).toBe(false);
  });

  test("a proposal has no stream to follow", () => {
    mockApi({
      [MARKET]: json(200, marketDetail({ status: "PROPOSED" })),
      [HISTORY]: json(200, history()),
    });
    const streams = installFakeEventSource();

    renderWithProviders(<Market enabled={false} />);

    expect(streams.instances).toHaveLength(0);
  });

  describe("when the stream fails", () => {
    beforeEach(() => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
    });
    afterEach(() => {
      vi.useRealTimers();
    });

    test("reads the market and its history at once and then every live_poll_fallback_seconds, with no error on screen", async () => {
      const { api, streams } = await open(marketDetail(), { pollSeconds: 4 });
      expect(api.calls(MARKET)).toHaveLength(1);

      act(() => streams.latest().fail());

      await waitFor(() => expect(api.calls(MARKET)).toHaveLength(2));
      await waitFor(() => expect(api.calls(HISTORY)).toHaveLength(2));
      expect(shown()).toContain("not live");
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();

      await act(() => vi.advanceTimersByTimeAsync(4_000));
      await waitFor(() => expect(api.calls(MARKET)).toHaveLength(3));
      // Each poll tries the stream again.
      expect(streams.instances).toHaveLength(2);
    });

    test("a poll that finds the market settled does what the status event would have done", async () => {
      const { api, streams, client } = await open(marketDetail(), { pollSeconds: 4 });
      client.setQueryData(keys.markets.list(["OPEN", "HALTED"]), { items: [] });
      api.on(
        MARKET,
        json(200, marketDetail({ status: "RESOLVED", outcome: "YES", trading_open: false })),
      );

      act(() => streams.latest().fail());

      await waitFor(() => expect(shown()).toContain("status RESOLVED"));
      await waitFor(() => expect(client.getQueryState(keys.me)?.isInvalidated).toBe(true));
      expect(client.getQueryState(keys.markets.list(["OPEN", "HALTED"]))?.isInvalidated).toBe(true);
    });

    test("a poll that finds the market as it was marks nothing else", async () => {
      const { api, streams, client } = await open(marketDetail(), { pollSeconds: 4 });

      act(() => streams.latest().fail());

      await waitFor(() => expect(api.calls(MARKET)).toHaveLength(2));
      await act(() => vi.advanceTimersByTimeAsync(50));
      expect(client.getQueryState(keys.me)?.isInvalidated).toBe(false);
    });

    test("keeps the screen current from the polls, and stops polling when the stream is back", async () => {
      const { api, streams } = await open(marketDetail(), { pollSeconds: 4 });
      api.on(MARKET, json(200, marketDetail({ p_yes: 0.6, p_no: 0.4, version: 12 })));

      act(() => streams.latest().fail());
      await waitFor(() => expect(shown()).toContain("p_yes 0.6"));

      await act(() => vi.advanceTimersByTimeAsync(4_000));
      act(() => streams.latest().open());
      const asked = api.calls(MARKET).length;
      await act(() => vi.advanceTimersByTimeAsync(20_000));

      expect(shown()).toContain("live");
      expect(api.calls(MARKET)).toHaveLength(asked);
    });
  });

  test("closes the stream when the screen goes away", async () => {
    const { streams, unmount } = await open();
    const stream = streams.latest();

    unmount();

    expect(stream.closed).toBe(true);
  });

  test("changes nothing for a market that has not been read yet", () => {
    mockApi({ [MARKET]: deferred().handler, [HISTORY]: deferred().handler });
    const streams = installFakeEventSource();
    const { client } = renderWithProviders(<Market />);
    act(() => streams.latest().open());

    act(() => streams.latest().emit("quote", quoteEvent({ version: 8 })));

    expect(
      client.getQueryData<MarketDetailResponse>(keys.markets.detail(MARKET_ID)),
    ).toBeUndefined();
    expect(screen.getByText("loading")).toBeInTheDocument();
  });
});
