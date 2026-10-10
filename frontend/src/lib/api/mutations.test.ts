import {
  MutationObserver,
  type MutationObserverOptions,
  QueryClient,
  type QueryKey,
  QueryObserver,
} from "@tanstack/react-query";
import { describe, expect, test } from "vitest";

import {
  adminUser,
  adminUsers,
  balanceAdjusted,
  BATCH_ID,
  codeBatch,
  codeBatchCodes,
  codeBatchCreated,
  ledgerItem,
  leaderboard,
  MARKET_ID,
  marketDetail,
  marketViewerWithPosition,
  me,
  notice,
  OTHER_MARKET_ID,
  OTHER_USER_ID,
  portfolio,
  profileSaved,
  proposal,
  tradeResponse,
  USER_ID,
  voucherCode,
  voucherIssued,
  voucherItem,
  voucherRedeemed,
  VOUCHER_ID,
} from "@/test/fixtures";
import { apiError, deferred, errorPage, json, mockApi, noAnswer, type Route } from "@/test/http";

import type { ApiError } from "./errors";
import { keys } from "./keys";
import {
  acknowledgeAlertMutation,
  adjustUserBalanceMutation,
  createCodeBatchMutation,
  issueVoucherMutation,
  markNoticeSeenMutation,
  proposeMarketMutation,
  redeemCodeMutation,
  redeemVoucherMutation,
  reinstateUserMutation,
  replaceVoucherCodeMutation,
  resolveMarketMutation,
  revokeCodesMutation,
  saveProfileMutation,
  setUserRoleMutation,
  suspendUserMutation,
  tradeMutation,
} from "./mutations";
import type {
  AdjustBalanceRequest,
  AdminUserListResponse,
  MarketDetailResponse,
  MeResponse,
  TradeRequest,
} from "./schema";
import { newSubmission } from "./submissions";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

const LEADERBOARD = keys.leaderboard.list({ sort: "credits" });
const OPEN_MARKETS = keys.markets.list(["OPEN", "HALTED"]);
/** Two lists of users that are on screen or were: everyone, and the suspended alone. */
const EVERY_USER = keys.admin.users.list({});
const SUSPENDED_USERS = keys.admin.users.list({ status: "SUSPENDED" });
const usersIn = (client: QueryClient, key: QueryKey) =>
  client
    .getQueryData<{ pages: AdminUserListResponse[] }>(key)
    ?.pages.flatMap((page) => page.items) ?? [];

/** A cache that holds every read a write could touch, so the test can see which it marked. */
function seeded(): QueryClient {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(keys.me, me({ balance_cr: "240" }));
  client.setQueryData(keys.positions, portfolio());
  client.setQueryData(keys.trades, { pages: [], pageParams: [] });
  client.setQueryData(keys.ledger, { pages: [], pageParams: [] });
  client.setQueryData(keys.proposals, { items: [] });
  client.setQueryData(keys.vouchers, { items: [voucherItem()] });
  client.setQueryData(keys.products, { items: [] });
  client.setQueryData(keys.markets.detail(MARKET_ID), marketDetail());
  client.setQueryData(keys.markets.history(MARKET_ID), { source: "postgres", points: [] });
  client.setQueryData(OPEN_MARKETS, { items: [] });
  client.setQueryData(LEADERBOARD, { pages: [leaderboard()], pageParams: [null] });
  client.setQueryData(keys.admin.batches, { items: [codeBatch()] });
  client.setQueryData(keys.admin.risk, {});
  client.setQueryData(EVERY_USER, { pages: [adminUsers()], pageParams: [null] });
  client.setQueryData(SUSPENDED_USERS, {
    pages: [{ items: [], next_cursor: null }],
    pageParams: [null],
  });
  return client;
}

function run<TData, TVariables>(
  client: QueryClient,
  options: MutationObserverOptions<TData, ApiError, TVariables>,
  variables: TVariables,
): Promise<TData> {
  return new MutationObserver(client, options).mutate(variables);
}

const stale = (client: QueryClient, key: QueryKey) =>
  client.getQueryState(key)?.isInvalidated ?? false;

/** Puts a read on screen, so that marking it out of date asks the API again. */
function onScreen(client: QueryClient, queryKey: QueryKey, queryFn: () => Promise<unknown>) {
  return new QueryObserver(client, { queryKey, queryFn, refetchOnMount: false }).subscribe(
    () => {},
  );
}

describe("saving the profile", () => {
  test("makes the answer the profile, without the note about this save", async () => {
    const saved = profileSaved({ username: "polyterrasse", signup_granted: true });
    const api = mockApi({ "PUT /api/me/profile": json(200, saved) });
    const client = seeded();

    await run(client, saveProfileMutation, {
      username: "polyterrasse",
      degree_level: "BACHELOR",
      degree_programme: "BSc Physics",
      policy_accepted: true,
    });

    // The save that creates the profile says that the usage policy is accepted.
    expect(api.lastCall("PUT /api/me/profile")?.body).toEqual({
      username: "polyterrasse",
      degree_level: "BACHELOR",
      degree_programme: "BSc Physics",
      policy_accepted: true,
    });
    const cached = client.getQueryData<MeResponse>(keys.me);
    expect(cached?.username).toBe("polyterrasse");
    expect(cached).not.toHaveProperty("signup_granted");
    expect(stale(client, LEADERBOARD)).toBe(true);
  });

  test("a save that changes a profile sends the three fields alone", async () => {
    const api = mockApi({
      "PUT /api/me/profile": json(200, profileSaved({ signup_granted: false })),
    });

    await run(seeded(), saveProfileMutation, {
      username: "anna_m",
      degree_level: "MASTER",
      degree_programme: "MSc Data Science",
    });

    expect(api.lastCall("PUT /api/me/profile")?.rawBody).toBe(
      '{"username":"anna_m","degree_level":"MASTER","degree_programme":"MSc Data Science"}',
    );
  });
});

describe("marking a notice seen", () => {
  const SEEN: Route = "POST /api/me/notices/{notice_id}/seen";
  const APPROVED = notice();
  const SECOND = notice({
    id: "5c1f0b7e-93a4-4d28-b6e1-2a7d9c4f8e13",
    market_id: OTHER_MARKET_ID,
    question: "Will the ASVZ open a second climbing wall before the end of the semester?",
    created_at: "2026-10-06T09:00:00Z",
  });

  /** Every read of the seeded cache: what a write could mark out of date. */
  const everyRead = (client: QueryClient) =>
    client
      .getQueryCache()
      .getAll()
      .map((query) => query.queryKey);

  /** A cache whose profile lists these notices, with the profile on screen. */
  function told(notices: MeResponse["notices"]) {
    const client = seeded();
    client.setQueryData(keys.me, me({ balance_cr: "240", notices }));
    const reads = { me: 0 };
    onScreen(client, keys.me, async () => {
      reads.me += 1;
      return me({ balance_cr: "240" });
    });
    return { client, reads };
  }

  test("names the notice in the address and sends no body", async () => {
    const api = mockApi({ [SEEN]: json(200, APPROVED) });
    const { client } = told([APPROVED]);

    const answer = await run(client, markNoticeSeenMutation, APPROVED.id);

    expect(answer).toEqual(APPROVED);
    const sent = api.lastCall(SEEN);
    expect(sent?.path).toBe(`/api/me/notices/${APPROVED.id}/seen`);
    expect(sent?.rawBody).toBe("");
  });

  test("on its answer the notice leaves the notices of the cached profile, and nothing else moves", async () => {
    const api = mockApi({ [SEEN]: json(200, APPROVED) });
    const { client, reads } = told([APPROVED, SECOND]);

    await run(client, markNoticeSeenMutation, APPROVED.id);

    const cached = client.getQueryData<MeResponse>(keys.me);
    expect(cached?.notices).toEqual([SECOND]);
    // The rest of the profile is as it was: the answer is the notice, not the profile.
    expect(cached).toEqual(me({ balance_cr: "240", notices: [SECOND] }));
    // The cache says what the next `/me` would say, so nothing is asked for.
    for (const key of everyRead(client))
      expect(stale(client, key), JSON.stringify(key)).toBe(false);
    expect(reads.me).toBe(0);
    expect(api.requests).toHaveLength(1);
  });

  test("the second of two notices leaves the list as the first did", async () => {
    mockApi({
      [SEEN]: (request) => json(200, request.path.includes(SECOND.id) ? SECOND : APPROVED),
    });
    const { client } = told([APPROVED, SECOND]);

    await Promise.all([
      run(client, markNoticeSeenMutation, APPROVED.id),
      run(client, markNoticeSeenMutation, SECOND.id),
    ]);

    expect(client.getQueryData<MeResponse>(keys.me)?.notices).toEqual([]);
  });

  test.each([
    ["no response", noAnswer, { kind: "network" }],
    ["an error page of the proxy", errorPage(502), { kind: "unavailable", status: 502 }],
    [
      "an error of the server",
      apiError(503, "SERVICE_UNAVAILABLE", "This is temporarily unavailable. Try again shortly."),
      { kind: "api", status: 503 },
    ],
  ])("after %s nothing is read and nothing is sent again", async (_name, handler, failure) => {
    const api = mockApi({ [SEEN]: handler });
    const { client, reads } = told([APPROVED, SECOND]);

    const error = await run(client, markNoticeSeenMutation, APPROVED.id).catch(
      (thrown: ApiError) => thrown,
    );
    // Whatever would follow by itself has had its turn.
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(error).toMatchObject({ ...failure, outcomeUnknown: true });
    // The notice stays in the list: the server still holds it as unseen.
    expect(client.getQueryData<MeResponse>(keys.me)?.notices).toEqual([APPROVED, SECOND]);
    for (const key of everyRead(client))
      expect(stale(client, key), JSON.stringify(key)).toBe(false);
    expect(reads.me).toBe(0);
    expect(api.requests).toHaveLength(1);
    expect(client.isMutating()).toBe(0);
  });

  test("a notice the API does not know as the caller's stays in the list, and nothing is read", async () => {
    const api = mockApi({ [SEEN]: apiError(404, "NOT_FOUND", "Not found.") });
    const { client, reads } = told([APPROVED]);

    await expect(run(client, markNoticeSeenMutation, APPROVED.id)).rejects.toMatchObject({
      code: "NOT_FOUND",
      message: "Not found.",
    });

    expect(client.getQueryData<MeResponse>(keys.me)?.notices).toEqual([APPROVED]);
    expect(stale(client, keys.me)).toBe(false);
    expect(reads.me).toBe(0);
    expect(api.requests).toHaveLength(1);
  });

  test("a cache without a profile is left without one", async () => {
    mockApi({ [SEEN]: json(200, APPROVED) });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    await run(client, markNoticeSeenMutation, APPROVED.id);

    expect(client.getQueryData(keys.me)).toBeUndefined();
  });
});

describe("a trade", () => {
  const request: TradeRequest = {
    side: "BUY",
    outcome: "YES",
    amount_cr: "25",
    min_shares: "45.12",
    quote_version: 7,
  };

  test("is serialised once and given one key", () => {
    const submission = newSubmission(MARKET_ID, request);

    expect(submission.target).toBe(MARKET_ID);
    expect(submission.body).toBe(JSON.stringify(request));
    expect(submission.idempotencyKey).toMatch(UUID);
    expect(newSubmission(MARKET_ID, request).idempotencyKey).not.toBe(submission.idempotencyKey);
  });

  test("goes out as the stored body with the stored key, and again unchanged", async () => {
    const api = mockApi({ "POST /api/markets/{market_id}/trades": errorPage(502) });
    const client = seeded();
    const submission = newSubmission(MARKET_ID, request);

    await run(client, tradeMutation, submission).catch(() => {});
    api.on("POST /api/markets/{market_id}/trades", json(201, tradeResponse()));
    await run(client, tradeMutation, submission);

    const [first, second] = api.calls("POST /api/markets/{market_id}/trades");
    expect(first?.path).toBe(`/api/markets/${MARKET_ID}/trades`);
    expect(first?.rawBody).toBe(submission.body);
    expect(first?.headers.get("Idempotency-Key")).toBe(submission.idempotencyKey);
    expect(first?.headers.get("Content-Type")).toBe("application/json");
    expect(second?.rawBody).toBe(first?.rawBody);
    expect(second?.headers.get("Idempotency-Key")).toBe(submission.idempotencyKey);
    // The amounts went out as the strings they came in as.
    expect(first?.body).toEqual(request);
  });

  test("puts the answer's balance and stake on screen and marks what it changed", async () => {
    mockApi({
      "POST /api/markets/{market_id}/trades": json(
        201,
        tradeResponse({
          balance_cr: "215",
          net_stake_cr: "25",
          stake_left_cr: "75",
          p_yes_after: 0.559,
          p_no_after: 0.441,
          market_version: 8,
        }),
      ),
    });
    const client = seeded();

    await run(client, tradeMutation, newSubmission(MARKET_ID, request));

    expect(client.getQueryData<MeResponse>(keys.me)?.balance_cr).toBe("215");
    const market = client.getQueryData<MarketDetailResponse>(keys.markets.detail(MARKET_ID));
    expect(market).toMatchObject({ p_yes: 0.559, p_no: 0.441, version: 8 });
    expect(market?.viewer).toMatchObject({
      balance_cr: "215",
      net_stake_cr: "25",
      stake_left_cr: "75",
    });
    for (const key of [
      keys.markets.detail(MARKET_ID),
      keys.markets.history(MARKET_ID),
      OPEN_MARKETS,
      keys.me,
      keys.positions,
      keys.trades,
      keys.ledger,
      LEADERBOARD,
    ]) {
      expect(stale(client, key), JSON.stringify(key)).toBe(true);
    }
    expect(stale(client, keys.admin.risk)).toBe(false);
  });

  test("does not move the price back when the stream was faster than the answer", async () => {
    mockApi({
      "POST /api/markets/{market_id}/trades": json(201, tradeResponse({ market_version: 8 })),
    });
    const client = seeded();
    client.setQueryData(
      keys.markets.detail(MARKET_ID),
      marketDetail({ p_yes: 0.61, p_no: 0.39, version: 9 }),
    );

    await run(client, tradeMutation, newSubmission(MARKET_ID, request));

    expect(client.getQueryData<MarketDetailResponse>(keys.markets.detail(MARKET_ID))).toMatchObject(
      { p_yes: 0.61, version: 9 },
    );
  });

  test("a refusal that means the screen is behind asks for the market again", async () => {
    const api = mockApi({
      "POST /api/markets/{market_id}/trades": apiError(
        422,
        "INSUFFICIENT_SHARES",
        "You cannot sell more shares than you hold.",
      ),
    });
    const client = seeded();

    await run(client, tradeMutation, newSubmission(MARKET_ID, request)).catch(() => {});
    expect(stale(client, keys.markets.detail(MARKET_ID))).toBe(true);
    expect(stale(client, keys.me)).toBe(false);

    api.on(
      "POST /api/markets/{market_id}/trades",
      apiError(422, "INSUFFICIENT_BALANCE", "Not enough tokens for this."),
    );
    await run(client, tradeMutation, newSubmission(MARKET_ID, request)).catch(() => {});
    expect(stale(client, keys.me)).toBe(true);
    expect(stale(client, keys.positions)).toBe(false);
  });

  test("a stale quote changes nothing in the cache", async () => {
    mockApi({
      "POST /api/markets/{market_id}/trades": apiError(409, "QUOTE_STALE", "The price moved."),
    });
    const client = seeded();

    await run(client, tradeMutation, newSubmission(MARKET_ID, request)).catch(() => {});

    expect(stale(client, keys.markets.detail(MARKET_ID))).toBe(false);
    expect(client.getQueryData<MeResponse>(keys.me)?.balance_cr).toBe("240");
  });
});

describe("redeeming a code", () => {
  test("sends the code alone and takes the balance from the answer", async () => {
    const api = mockApi({
      "POST /api/codes/redeem": json(200, { value_cr: "35", balance_cr: "275" }),
    });
    const client = seeded();

    const redeemed = await run(client, redeemCodeMutation, "4F7K-Q2M9-XAB3");

    expect(redeemed.value_cr).toBe("35");
    expect(api.lastCall("POST /api/codes/redeem")?.body).toEqual({ code: "4F7K-Q2M9-XAB3" });
    expect(client.getQueryData<MeResponse>(keys.me)?.balance_cr).toBe("275");
    for (const key of [keys.me, keys.ledger, keys.products, LEADERBOARD]) {
      expect(stale(client, key), JSON.stringify(key)).toBe(true);
    }
  });

  test("a refusal changes nothing", async () => {
    mockApi({
      "POST /api/codes/redeem": apiError(422, "CODE_INVALID", "This code is not valid."),
    });
    const client = seeded();

    await expect(run(client, redeemCodeMutation, "NOPE")).rejects.toMatchObject({
      code: "CODE_INVALID",
      message: "This code is not valid.",
    });

    expect(stale(client, keys.me)).toBe(false);
    expect(stale(client, keys.ledger)).toBe(false);
  });

  test("without an answer the user may not act again before the balance and the activity are read afresh", async () => {
    const meAnswer = deferred();
    const ledgerAnswer = deferred();
    mockApi({ "POST /api/codes/redeem": noAnswer });
    const client = seeded();
    onScreen(client, keys.me, () => meAnswer.handler().then((response) => response.json()));
    onScreen(client, keys.ledger, () => ledgerAnswer.handler().then((response) => response.json()));

    const observer = new MutationObserver(client, redeemCodeMutation);
    const attempt = observer.mutate("4F7K-Q2M9-XAB3").catch((error: ApiError) => error);
    await Promise.resolve();
    await new Promise((resolve) => setTimeout(resolve, 0));

    // The request failed, both reads are on their way: still pending.
    expect(observer.getCurrentResult().isPending).toBe(true);

    meAnswer.resolve(json(200, me({ balance_cr: "275" })));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(observer.getCurrentResult().isPending).toBe(true);

    ledgerAnswer.resolve(json(200, { items: [ledgerItem()], next_cursor: null }));
    const error = await attempt;

    expect(error).toMatchObject({ kind: "network" });
    expect(observer.getCurrentResult().isPending).toBe(false);
    // The balance on screen is what the server recorded.
    expect(client.getQueryData<MeResponse>(keys.me)?.balance_cr).toBe("275");
  });
});

describe("vouchers", () => {
  test("getting one sends the product's id and takes the balance from the answer", async () => {
    const api = mockApi({ "POST /api/vouchers": json(201, voucherIssued({ balance_cr: "205" })) });
    const client = seeded();

    const voucher = await run(client, issueVoucherMutation, "coffee");

    expect(voucher.code).toBe("7KQ2M9XA");
    expect(api.lastCall("POST /api/vouchers")?.body).toEqual({ product_id: "coffee" });
    expect(client.getQueryData<MeResponse>(keys.me)?.balance_cr).toBe("205");
    for (const key of [keys.me, keys.vouchers, keys.ledger, keys.products, LEADERBOARD]) {
      expect(stale(client, key), JSON.stringify(key)).toBe(true);
    }
  });

  test("an issue without an answer reads the vouchers and the profile before it settles", async () => {
    mockApi({ "POST /api/vouchers": errorPage(504) });
    const client = seeded();
    let vouchersRead = 0;
    let meRead = 0;
    onScreen(client, keys.vouchers, async () => {
      vouchersRead += 1;
      return { items: [voucherItem(), voucherItem({ id: "issued-after-all" })] };
    });
    onScreen(client, keys.me, async () => {
      meRead += 1;
      return me({ balance_cr: "205" });
    });

    await run(client, issueVoucherMutation, "coffee").catch(() => {});

    // By the time the mutation is over, the list shows whether the voucher was issued.
    expect(vouchersRead).toBe(1);
    expect(meRead).toBe(1);
    expect(client.getQueryData<{ items: unknown[] }>(keys.vouchers)?.items).toHaveLength(2);
  });

  test("not enough tokens reads the products and the profile again", async () => {
    mockApi({
      "POST /api/vouchers": apiError(422, "INSUFFICIENT_BALANCE", "Not enough tokens for this."),
    });
    const client = seeded();

    await run(client, issueVoucherMutation, "coffee").catch(() => {});

    expect(stale(client, keys.products)).toBe(true);
    expect(stale(client, keys.me)).toBe(true);
    expect(stale(client, keys.vouchers)).toBe(false);
  });

  test("a fresh code is asked for by voucher id, with no body", async () => {
    const api = mockApi({ "POST /api/vouchers/{voucher_id}/code": json(200, voucherCode()) });
    const client = seeded();

    const fresh = await run(client, replaceVoucherCodeMutation, VOUCHER_ID);

    const sent = api.lastCall("POST /api/vouchers/{voucher_id}/code");
    expect(sent?.path).toBe(`/api/vouchers/${VOUCHER_ID}/code`);
    expect(sent?.rawBody).toBe("");
    expect(fresh.code).toBe("B4T8WN3C");
    expect(stale(client, keys.vouchers)).toBe(false);
  });

  test("a voucher that is used up or gone marks the list out of date", async () => {
    const api = mockApi({
      "POST /api/vouchers/{voucher_id}/code": apiError(
        409,
        "CONFLICT",
        "This is not possible in the current state.",
      ),
    });
    const client = seeded();

    await run(client, replaceVoucherCodeMutation, VOUCHER_ID).catch(() => {});
    expect(stale(client, keys.vouchers)).toBe(true);

    const other = seeded();
    api.on("POST /api/vouchers/{voucher_id}/code", apiError(404, "NOT_FOUND", "Not found."));
    await run(other, replaceVoucherCodeMutation, VOUCHER_ID).catch(() => {});
    expect(stale(other, keys.vouchers)).toBe(true);
  });

  test("the barista sends the code in the body, never in the address", async () => {
    const api = mockApi({ "POST /api/barista/vouchers/redeem": json(200, voucherRedeemed()) });

    const result = await run(seeded(), redeemVoucherMutation, "7KQ2-M9XA");

    const sent = api.lastCall("POST /api/barista/vouchers/redeem");
    expect(sent?.path).toBe("/api/barista/vouchers/redeem");
    expect(sent?.url).not.toContain("7KQ2");
    expect(sent?.body).toEqual({ code: "7KQ2-M9XA" });
    expect(result.product_name).toBe("Coffee");
  });
});

describe("markets", () => {
  test("a proposal puts its market in the cache and marks the lists it belongs to", async () => {
    const proposed = marketDetail({ id: "new-market", status: "PROPOSED", trading_open: false });
    mockApi({ "POST /api/markets": json(201, proposed) });
    const client = seeded();
    const earlier = proposal({ status: "OPEN" });
    client.setQueryData(keys.proposals, { items: [earlier] });

    await run(client, proposeMarketMutation, {
      question: proposed.question,
      yes_definition: proposed.yes_definition,
      no_definition: proposed.no_definition,
      resolution_criteria: proposed.resolution_criteria,
      resolution_source: proposed.resolution_source,
      close_at: "2026-12-11T18:00:00+01:00",
      resolve_by: "2026-12-18T18:00:00+01:00",
      policy_accepted: true,
    });

    expect(client.getQueryData(keys.markets.detail("new-market"))).toEqual(proposed);
    // At the top of the caller's proposals before the list has been read again, as a row
    // of that list: nothing of the market that the list does not have.
    expect(client.getQueryData(keys.proposals)).toEqual({
      items: [
        {
          id: "new-market",
          question: proposed.question,
          yes_definition: proposed.yes_definition,
          no_definition: proposed.no_definition,
          resolution_criteria: proposed.resolution_criteria,
          resolution_source: proposed.resolution_source,
          close_at: proposed.close_at,
          resolve_by: proposed.resolve_by,
          status: "PROPOSED",
          outcome: null,
          rejection_reason: proposed.rejection_reason,
          created_at: proposed.created_at,
        },
        earlier,
      ],
    });
    expect(stale(client, keys.proposals)).toBe(true);
    expect(stale(client, OPEN_MARKETS)).toBe(true);
  });

  test("a proposal the list already holds is not listed twice", async () => {
    const proposed = marketDetail({ id: "new-market", status: "PROPOSED", trading_open: false });
    mockApi({ "POST /api/markets": json(201, proposed) });
    const client = seeded();
    client.setQueryData(keys.proposals, {
      items: [proposal({ status: "OPEN" }), proposal({ id: "new-market" })],
    });

    await run(client, proposeMarketMutation, {
      question: proposed.question,
      yes_definition: proposed.yes_definition,
      no_definition: proposed.no_definition,
      resolution_criteria: proposed.resolution_criteria,
      resolution_source: proposed.resolution_source,
      close_at: "2026-12-11T18:00:00+01:00",
      resolve_by: "2026-12-18T18:00:00+01:00",
      policy_accepted: true,
    });

    const listed = client.getQueryData<{ items: { id: string }[] }>(keys.proposals);
    expect(listed?.items.map((item) => item.id)).toEqual(["new-market", proposal().id]);
  });

  test("an admin action makes its answer the market and marks what settles with it", async () => {
    const resolved = marketDetail({
      status: "RESOLVED",
      trading_open: false,
      outcome: "YES",
      viewer: marketViewerWithPosition({ payout_cr: "91.645" }),
    });
    const api = mockApi({ "POST /api/admin/markets/{market_id}/resolve": json(200, resolved) });
    const client = seeded();

    await run(client, resolveMarketMutation, {
      marketId: MARKET_ID,
      body: { outcome: "YES", evidence: "No interruption was reported." },
    });

    const sent = api.lastCall("POST /api/admin/markets/{market_id}/resolve");
    expect(sent?.path).toBe(`/api/admin/markets/${MARKET_ID}/resolve`);
    expect(sent?.body).toEqual({ outcome: "YES", evidence: "No interruption was reported." });
    expect(client.getQueryData(keys.markets.detail(MARKET_ID))).toEqual(resolved);
    for (const key of [OPEN_MARKETS, keys.admin.risk, keys.me, keys.positions, LEADERBOARD]) {
      expect(stale(client, key), JSON.stringify(key)).toBe(true);
    }
  });

  test("an action the market's state refuses reads the market again", async () => {
    mockApi({
      "POST /api/admin/markets/{market_id}/resolve": apiError(
        403,
        "FORBIDDEN",
        "You traded this market, so another admin has to settle it.",
      ),
    });
    const client = seeded();

    await expect(
      run(client, resolveMarketMutation, {
        marketId: MARKET_ID,
        body: { outcome: "NO", evidence: "x" },
      }),
    ).rejects.toMatchObject({
      message: "You traded this market, so another admin has to settle it.",
    });

    expect(stale(client, keys.markets.detail(MARKET_ID))).toBe(true);
  });
});

describe("codes and alerts", () => {
  test("a new batch marks the batches and the risk figures", async () => {
    mockApi({ "POST /api/admin/code-batches": json(201, codeBatchCreated()) });
    const client = seeded();

    const created = await run(client, createCodeBatchMutation, {
      label: "Welcome week",
      count: 2,
      value_cr: "35",
      expires_at: "2026-12-31T23:59:00+01:00",
      one_per_user: true,
    });

    expect(created.codes).toHaveLength(2);
    expect(stale(client, keys.admin.batches)).toBe(true);
    expect(stale(client, keys.admin.risk)).toBe(true);
  });

  test("revoking a batch sends no list of codes, and forgets the codes that were read of it", async () => {
    const api = mockApi({
      "POST /api/admin/code-batches/{batch_id}/revoke": json(200, {
        batch: codeBatch({ active_count: 0, revoked_count: 14 }),
        revoked_count: 14,
      }),
    });
    const client = seeded();
    client.setQueryData(keys.admin.batchCodes(BATCH_ID), codeBatchCodes());
    client.setQueryData(keys.admin.batchCodes("another-batch"), codeBatchCodes());

    await run(client, revokeCodesMutation, { batchId: BATCH_ID });

    const sent = api.lastCall("POST /api/admin/code-batches/{batch_id}/revoke");
    expect(sent?.path).toBe(`/api/admin/code-batches/${BATCH_ID}/revoke`);
    expect(sent?.body).toEqual({});
    expect(stale(client, keys.admin.batches)).toBe(true);
    expect(client.getQueryData(keys.admin.batchCodes(BATCH_ID))).toBeUndefined();
    expect(client.getQueryData(keys.admin.batchCodes("another-batch"))).toEqual(codeBatchCodes());
  });

  test("marking the batches out of date does not touch codes that were read: every read of them is recorded", async () => {
    mockApi({ "POST /api/admin/code-batches": json(201, codeBatchCreated()) });
    const client = seeded();
    client.setQueryData(keys.admin.batchCodes(BATCH_ID), codeBatchCodes());

    await run(client, createCodeBatchMutation, {
      label: "Welcome week",
      count: 2,
      value_cr: "35",
      expires_at: "2026-12-31T23:59:00+01:00",
      one_per_user: true,
    });

    expect(stale(client, keys.admin.batches)).toBe(true);
    expect(stale(client, keys.admin.batchCodes(BATCH_ID))).toBe(false);
  });

  test("acknowledging an alert marks the risk figures", async () => {
    const api = mockApi({
      "POST /api/admin/alerts/{alert_id}/ack": json(200, { id: 3 }),
    });
    const client = seeded();

    await run(client, acknowledgeAlertMutation, 3);

    expect(api.lastCall("POST /api/admin/alerts/{alert_id}/ack")?.path).toBe(
      "/api/admin/alerts/3/ack",
    );
    expect(stale(client, keys.admin.risk)).toBe(true);
    expect(stale(client, keys.admin.batches)).toBe(false);
  });
});

describe("users", () => {
  const SUSPEND: Route = "POST /api/admin/users/{user_id}/suspend";
  const REINSTATE: Route = "POST /api/admin/users/{user_id}/reinstate";
  const ROLE: Route = "POST /api/admin/users/{user_id}/role";
  const ADJUST: Route = "POST /api/admin/users/{user_id}/adjustments";

  test("suspending sends the reason alone, shows the answer as the user's row and marks the lists and the leaderboard", async () => {
    const suspended = adminUser({ status: "SUSPENDED" });
    const api = mockApi({ [SUSPEND]: json(200, suspended) });
    const client = seeded();

    const answer = await run(client, suspendUserMutation, {
      userId: OTHER_USER_ID,
      reason: "Traded through another person's account.",
    });

    expect(answer).toEqual(suspended);
    expect(api.lastCall(SUSPEND)?.path).toBe(`/api/admin/users/${OTHER_USER_ID}/suspend`);
    expect(api.lastCall(SUSPEND)?.body).toEqual({
      reason: "Traded through another person's account.",
    });
    // The row is the answer at once, in the place it had; the other row is as it was.
    expect(usersIn(client, EVERY_USER)).toEqual([suspended, adminUsers().items[1]]);
    // A list that is filtered may hold other rows from here on: both are read again.
    expect(stale(client, EVERY_USER)).toBe(true);
    expect(stale(client, SUSPENDED_USERS)).toBe(true);
    expect(stale(client, LEADERBOARD)).toBe(true);
    expect(stale(client, keys.admin.risk)).toBe(false);
    expect(stale(client, keys.me)).toBe(false);
  });

  test("reinstating does the same with its own route", async () => {
    const reinstated = adminUser({ status: "ACTIVE" });
    const api = mockApi({ [REINSTATE]: json(200, reinstated) });
    const client = seeded();
    client.setQueryData(EVERY_USER, {
      pages: [adminUsers({ items: [adminUser({ status: "SUSPENDED" })] })],
      pageParams: [null],
    });

    await run(client, reinstateUserMutation, { userId: OTHER_USER_ID, reason: "Cleared up." });

    expect(api.lastCall(REINSTATE)?.path).toBe(`/api/admin/users/${OTHER_USER_ID}/reinstate`);
    expect(api.lastCall(REINSTATE)?.body).toEqual({ reason: "Cleared up." });
    expect(usersIn(client, EVERY_USER)).toEqual([reinstated]);
    expect(stale(client, EVERY_USER)).toBe(true);
    expect(stale(client, LEADERBOARD)).toBe(true);
  });

  test("a role is sent as its key, and the leaderboard is left alone", async () => {
    const barista = adminUser({ role: "BARISTA" });
    const api = mockApi({ [ROLE]: json(200, barista) });
    const client = seeded();

    await run(client, setUserRoleMutation, { userId: OTHER_USER_ID, role: "BARISTA" });

    expect(api.lastCall(ROLE)?.path).toBe(`/api/admin/users/${OTHER_USER_ID}/role`);
    expect(api.lastCall(ROLE)?.body).toEqual({ role: "BARISTA" });
    expect(usersIn(client, EVERY_USER)[0]).toEqual(barista);
    expect(stale(client, EVERY_USER)).toBe(true);
    expect(stale(client, LEADERBOARD)).toBe(false);
  });

  test("a row on a later page is found too", async () => {
    const barista = adminUser({ role: "BARISTA" });
    mockApi({ [ROLE]: json(200, barista) });
    const client = seeded();
    client.setQueryData(EVERY_USER, {
      pages: [
        { items: [adminUsers().items[1]], next_cursor: "page-2" },
        { items: [adminUser()], next_cursor: null },
      ],
      pageParams: [null, "page-2"],
    });

    await run(client, setUserRoleMutation, { userId: OTHER_USER_ID, role: "BARISTA" });

    expect(usersIn(client, EVERY_USER)).toEqual([adminUsers().items[1], barista]);
  });

  test.each([
    ["CONFLICT", apiError(409, "CONFLICT", "This account is already suspended.")],
    ["NOT_FOUND", apiError(404, "NOT_FOUND", "Not found.")],
  ] as const)("a %s means the list is behind: it is read again", async (_code, refusal) => {
    mockApi({ [SUSPEND]: refusal });
    const client = seeded();

    await run(client, suspendUserMutation, { userId: OTHER_USER_ID, reason: "x" }).catch(() => {});

    expect(stale(client, EVERY_USER)).toBe(true);
    expect(usersIn(client, EVERY_USER)).toEqual(adminUsers().items);
  });

  test("a refusal that says who may not be changed marks nothing", async () => {
    mockApi({
      [ROLE]: apiError(
        403,
        "FORBIDDEN",
        "This account is an admin by the server's configuration. Its role cannot be changed here.",
      ),
    });
    const client = seeded();

    await expect(
      run(client, setUserRoleMutation, { userId: USER_ID, role: "USER" }),
    ).rejects.toMatchObject({ code: "FORBIDDEN" });

    expect(stale(client, EVERY_USER)).toBe(false);
  });

  test("without an answer the user may not act again before the list has been read afresh", async () => {
    const listAnswer = deferred();
    mockApi({ [SUSPEND]: noAnswer });
    const client = seeded();
    onScreen(client, EVERY_USER, () => listAnswer.handler().then((response) => response.json()));

    const observer = new MutationObserver(client, suspendUserMutation);
    const attempt = observer
      .mutate({ userId: OTHER_USER_ID, reason: "x" })
      .catch((error: ApiError) => error);
    await new Promise((resolve) => setTimeout(resolve, 0));

    // The request failed, the list is on its way: still pending.
    expect(observer.getCurrentResult().isPending).toBe(true);

    const held = adminUsers({ items: [adminUser({ status: "SUSPENDED" })] });
    listAnswer.resolve(json(200, { pages: [held], pageParams: [null] }));
    const error = await attempt;

    expect(error).toMatchObject({ kind: "network" });
    expect(observer.getCurrentResult().isPending).toBe(false);
    // The row on screen is what the server holds.
    expect(usersIn(client, EVERY_USER)).toEqual(held.items);
  });

  describe("an adjustment of tokens", () => {
    const request: AdjustBalanceRequest = {
      amount_cr: "-35.5",
      reason: "Booked twice by mistake.",
    };

    test("goes out as the stored body with the stored key, and again unchanged", async () => {
      const api = mockApi({ [ADJUST]: errorPage(502) });
      const client = seeded();
      const submission = newSubmission(OTHER_USER_ID, request);

      await run(client, adjustUserBalanceMutation, submission).catch(() => {});
      api.on(ADJUST, json(201, balanceAdjusted({ amount_cr: "-35.5", balance_cr: "944.5" })));
      await run(client, adjustUserBalanceMutation, submission);

      const [first, second] = api.calls(ADJUST);
      expect(first?.path).toBe(`/api/admin/users/${OTHER_USER_ID}/adjustments`);
      expect(first?.rawBody).toBe('{"amount_cr":"-35.5","reason":"Booked twice by mistake."}');
      expect(first?.rawBody).toBe(submission.body);
      expect(first?.headers.get("Idempotency-Key")).toBe(submission.idempotencyKey);
      expect(first?.headers.get("Idempotency-Key")).toMatch(UUID);
      expect(first?.headers.get("Content-Type")).toBe("application/json");
      expect(second?.rawBody).toBe(first?.rawBody);
      expect(second?.headers.get("Idempotency-Key")).toBe(submission.idempotencyKey);
    });

    test("puts the answer's balance into the user's row and marks what the tokens changed", async () => {
      mockApi({ [ADJUST]: json(201, balanceAdjusted({ balance_cr: "1015" })) });
      const client = seeded();

      await run(client, adjustUserBalanceMutation, newSubmission(OTHER_USER_ID, request));

      const [adjusted, other] = usersIn(client, EVERY_USER);
      // The balance is the string of the answer; the rest of the row is as it was.
      expect(adjusted).toEqual({ ...adminUser(), balance_cr: "1015" });
      expect(other).toEqual(adminUsers().items[1]);
      expect(stale(client, keys.admin.risk)).toBe(true);
      expect(stale(client, LEADERBOARD)).toBe(true);
      // The row is the answer: the list is not asked for again.
      expect(stale(client, EVERY_USER)).toBe(false);
      expect(client.getQueryData<MeResponse>(keys.me)?.balance_cr).toBe("240");
    });

    test("a removal of more than the account holds reads the list again", async () => {
      mockApi({
        [ADJUST]: apiError(
          409,
          "INSUFFICIENT_BALANCE",
          "This account holds fewer tokens than that.",
          { balance_cr: "20", required_cr: "35.5" },
        ),
      });
      const client = seeded();

      await run(client, adjustUserBalanceMutation, newSubmission(OTHER_USER_ID, request)).catch(
        () => {},
      );

      expect(stale(client, EVERY_USER)).toBe(true);
      expect(stale(client, keys.admin.risk)).toBe(false);
    });

    test("without an answer nothing is read again: the same request is what is sent again", async () => {
      mockApi({ [ADJUST]: noAnswer });
      const client = seeded();

      await run(client, adjustUserBalanceMutation, newSubmission(OTHER_USER_ID, request)).catch(
        () => {},
      );

      expect(stale(client, EVERY_USER)).toBe(false);
      expect(stale(client, keys.admin.risk)).toBe(false);
    });
  });
});
