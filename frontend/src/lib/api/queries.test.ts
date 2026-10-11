import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { describe, expect, test, vi } from "vitest";

import {
  adminUser,
  adminUsers,
  BATCH_ID,
  codeBatchCodes,
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
import { keys } from "./keys";
import {
  adminUsersQuery,
  batchCodesQuery,
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

    expect(loadedConfig.share_presets).toEqual(["25", "50", "100"]);
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

  test("a quote is asked by shares on both sides, with the characters it was given", async () => {
    const api = mockApi({ "GET /api/markets/{market_id}/quote": json(200, quote()) });
    const queries = client();

    await queries.fetchQuery(quoteQuery(MARKET_ID, { side: "BUY", outcome: "YES", shares: "50" }));
    await queries.fetchQuery(
      quoteQuery(MARKET_ID, { side: "SELL", outcome: "NO", shares: "45.8225" }),
    );

    const [buy, sell] = api.calls("GET /api/markets/{market_id}/quote");
    expect(buy?.query.toString()).toBe("side=BUY&outcome=YES&shares=50");
    expect(sell?.query.toString()).toBe("side=SELL&outcome=NO&shares=45.8225");
  });

  test("the key of a quote holds its request: side, outcome and shares", () => {
    const asked = quoteQuery(MARKET_ID, { side: "BUY", outcome: "YES", shares: "50" });

    expect(asked.queryKey).toEqual([
      "markets",
      "quote",
      MARKET_ID,
      { side: "BUY", outcome: "YES", shares: "50" },
    ]);
    expect(asked.queryKey.slice(0, 3)).toEqual(keys.markets.quotes(MARKET_ID));
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

describe("the admin's reads", () => {
  test("the users are asked for with the search and the filters under the API's names, page by page", async () => {
    const api = mockApi({
      "GET /api/admin/users": (request) =>
        request.query.get("cursor") === null
          ? json(200, adminUsers({ next_cursor: "page-2" }))
          : json(200, adminUsers({ items: [adminUser({ id: "second" })], next_cursor: null })),
    });
    const queries = client();
    const users = adminUsersQuery({ q: "bea", role: "USER", status: "ACTIVE" });

    const both = await queries.fetchInfiniteQuery({ ...users, pages: 2 });

    expect(both.pages.map((page) => page.items[0]?.id)).toEqual([adminUser().id, "second"]);
    const sent = api.calls("GET /api/admin/users").map((request) => request.query.toString());
    // No page size: the list is as long as the API makes it.
    expect(sent).toEqual([
      "q=bea&role=USER&status=ACTIVE",
      "q=bea&role=USER&status=ACTIVE&cursor=page-2",
    ]);
    expect(users.queryKey).toEqual([
      "admin",
      "users",
      { q: "bea", role: "USER", status: "ACTIVE" },
    ]);
  });

  test("an empty search and no filter send nothing at all", async () => {
    const api = mockApi({ "GET /api/admin/users": json(200, adminUsers()) });

    await client().fetchInfiniteQuery(adminUsersQuery());

    expect(api.lastCall("GET /api/admin/users")?.query.toString()).toBe("");
  });

  test("the codes of a batch are asked for by its id", async () => {
    const api = mockApi({
      "GET /api/admin/code-batches/{batch_id}/codes": json(200, codeBatchCodes()),
    });

    const answer = await client().fetchQuery(batchCodesQuery(BATCH_ID));

    expect(api.lastCall("GET /api/admin/code-batches/{batch_id}/codes")?.path).toBe(
      `/api/admin/code-batches/${BATCH_ID}/codes`,
    );
    expect(answer.codes.map((entry) => entry.code)).toEqual(["4F7KQ2M9XAB3", "T8WN3CB4Z6PD"]);
    expect(answer.batch.label).toBe("Welcome week");
  });

  test("the codes are read when they are asked for and never behind the admin's back: every read is recorded", () => {
    const codes = batchCodesQuery(BATCH_ID);

    // Not when the window gets the focus back, not when the connection is back, and not
    // because time has passed: a read that is on screen stays as it is.
    expect(codes.refetchOnWindowFocus).toBe(false);
    expect(codes.refetchOnReconnect).toBe(false);
    expect(codes.staleTime).toBe(Infinity);
    // Not under the batches, which are marked out of date after every write.
    expect(codes.queryKey.slice(0, 2)).not.toEqual(keys.admin.batches);
  });

  test("the codes are dropped from memory as soon as nothing shows them", async () => {
    mockApi({ "GET /api/admin/code-batches/{batch_id}/codes": json(200, codeBatchCodes()) });
    const queries = client();
    const codes = batchCodesQuery(BATCH_ID);
    const observer = new QueryObserver(queries, codes);

    const stop = observer.subscribe(() => {});
    await vi.waitFor(() => expect(queries.getQueryData(codes.queryKey)).toBeDefined());

    stop();
    await vi.waitFor(() => expect(queries.getQueryData(codes.queryKey)).toBeUndefined());
  });
});
