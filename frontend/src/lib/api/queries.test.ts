import { QueryClient } from "@tanstack/react-query";
import { describe, expect, test } from "vitest";

import {
  config,
  leaderboard,
  ledgerItem,
  MARKET_ID,
  marketDetail,
  marketSummary,
  me,
  quote,
  tradeItem,
} from "@/test/fixtures";
import { apiError, json, mockApi } from "@/test/http";

import { ApiError } from "./errors";
import {
  configQuery,
  leaderboardQuery,
  ledgerQuery,
  marketQuery,
  marketsQuery,
  meQuery,
  quoteQuery,
  tradesQuery,
} from "./queries";

const client = () => new QueryClient({ defaultOptions: { queries: { retry: false } } });

describe("reads", () => {
  test("config and me come back typed and unchanged", async () => {
    mockApi({ "GET /api/config": json(200, config()), "GET /api/me": json(200, me()) });
    const queries = client();

    const loadedConfig = await queries.fetchQuery(configQuery);
    const loadedMe = await queries.fetchQuery(meQuery);

    expect(loadedConfig.stake_presets_cr).toEqual(["25", "50", "100"]);
    expect(loadedMe.balance_cr).toBe("240");
  });

  test("a market list sends each status as its own parameter, or none at all", async () => {
    const api = mockApi({ "GET /api/markets": json(200, { items: [marketSummary()] }) });
    const queries = client();

    await queries.fetchQuery(marketsQuery(["OPEN", "HALTED"]));
    await queries.fetchQuery(marketsQuery());

    const [filtered, everything] = api.calls("GET /api/markets");
    expect(filtered?.query.getAll("status")).toEqual(["OPEN", "HALTED"]);
    expect(everything?.query.toString()).toBe("");
  });

  test("a market is asked for by its id", async () => {
    const api = mockApi({ "GET /api/markets/{market_id}": json(200, marketDetail()) });

    const market = await client().fetchQuery(marketQuery(MARKET_ID));

    expect(api.lastCall("GET /api/markets/{market_id}")?.path).toBe(`/api/markets/${MARKET_ID}`);
    expect(market.viewer.stake_left_cr).toBe("100");
  });

  test("a quote sends the preset as it arrived: a budget when buying, shares when selling", async () => {
    const api = mockApi({ "GET /api/markets/{market_id}/quote": json(200, quote()) });
    const queries = client();

    await queries.fetchQuery(
      quoteQuery(MARKET_ID, { side: "BUY", outcome: "YES", amount_cr: "25" }),
    );
    await queries.fetchQuery(
      quoteQuery(MARKET_ID, { side: "SELL", outcome: "NO", shares: "45.8225" }),
    );

    const [buy, sell] = api.calls("GET /api/markets/{market_id}/quote");
    expect(buy?.query.toString()).toBe("side=BUY&outcome=YES&amount_cr=25");
    expect(sell?.query.toString()).toBe("side=SELL&outcome=NO&shares=45.8225");
  });

  test("a failed read throws the API's error", async () => {
    mockApi({ "GET /api/markets/{market_id}": apiError(404, "NOT_FOUND", "Not found.") });

    const failed = client().fetchQuery(marketQuery("no-such-market"));

    await expect(failed).rejects.toBeInstanceOf(ApiError);
    await expect(failed).rejects.toMatchObject({ code: "NOT_FOUND", message: "Not found." });
  });
});

describe("lists that page", () => {
  test("ask for the next page with next_cursor and never send a page size", async () => {
    const api = mockApi({
      "GET /api/me/trades": (request) =>
        request.query.get("cursor") === null
          ? json(200, { items: [tradeItem()], next_cursor: "page-2" })
          : json(200, { items: [tradeItem({ id: "second" })], next_cursor: null }),
    });
    const queries = client();

    const first = await queries.fetchInfiniteQuery(tradesQuery);
    expect(first.pages).toHaveLength(1);
    expect(tradesQuery.getNextPageParam(first.pages[0]!, first.pages, null, [null])).toBe("page-2");

    // Two pages: the first again, then the one its next_cursor names.
    const both = await queries.fetchInfiniteQuery({ ...tradesQuery, pages: 2 });

    expect(both.pages.map((page) => page.items[0]?.id)).toEqual([tradeItem().id, "second"]);
    const sent = api.calls("GET /api/me/trades").map((request) => request.query.toString());
    expect(sent).toEqual(["", "", "cursor=page-2"]);
  });

  test("the last page has no next page", async () => {
    mockApi({ "GET /api/me/ledger": json(200, { items: [ledgerItem()], next_cursor: null }) });

    const data = await client().fetchInfiniteQuery(ledgerQuery);

    expect(ledgerQuery.getNextPageParam(data.pages[0]!, data.pages, null, [null])).toBeUndefined();
  });

  test("the leaderboard sends sort, order and filters under the API's names", async () => {
    const api = mockApi({ "GET /api/leaderboard": json(200, leaderboard()) });

    await client().fetchInfiniteQuery(
      leaderboardQuery({
        sort: "win_rate",
        order: "asc",
        degree_level: "MASTER",
        min_credits_cr: "100.5",
      }),
    );

    const query = api.lastCall("GET /api/leaderboard")?.query;
    expect(Object.fromEntries(query ?? [])).toEqual({
      sort: "win_rate",
      order: "asc",
      degree_level: "MASTER",
      min_credits_cr: "100.5",
    });
  });
});
