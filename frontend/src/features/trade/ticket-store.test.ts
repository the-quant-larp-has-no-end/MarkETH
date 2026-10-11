import { onlineManager, QueryObserver } from "@tanstack/react-query";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { GENERIC_MESSAGE } from "@/lib/api/errors";
import { keys } from "@/lib/api/keys";
import { page } from "@/lib/browser";
import type { MarketDetailResponse, MeResponse } from "@/lib/api/schema";
import {
  MARKET_ID,
  marketDetail,
  marketViewerWithPosition,
  me,
  OTHER_MARKET_ID,
  quote,
  tradeResponse,
} from "@/test/fixtures";
import {
  apiError,
  deferred,
  errorPage,
  type Handler,
  json,
  mockApi,
  noAnswer,
  portal401,
  type Route,
} from "@/test/http";
import { createTestQueryClient } from "@/test/render";

import { choiceKey, type Order, ticketStore, type TicketStore } from "./ticket-store";

const TRADE: Route = "POST /api/markets/{market_id}/trades";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

const viewer = marketViewerWithPosition();

/** Buying YES for 25, as the amount field held it, guarded by the quote of the fixtures. */
const buy: Order = {
  intent: { side: "BUY", outcome: "YES" },
  request: { side: "BUY", outcome: "YES", amount_cr: "25" },
  quote: quote(),
  buy: viewer.buy,
  sellPresets: viewer.sell_presets,
  sell: viewer.sell,
};

/** Selling half of the YES holding. */
const sell: Order = {
  intent: { side: "SELL", outcome: "YES" },
  request: { side: "SELL", outcome: "YES", shares: "45.8225" },
  quote: quote({ side: "SELL", shares: "45.8225", amount_cr: "23.874512", quote_version: 9 }),
  buy: viewer.buy,
  sellPresets: viewer.sell_presets,
  sell: viewer.sell,
};

function setup(trade: Handler) {
  const api = mockApi({ [TRADE]: trade });
  const client = createTestQueryClient();
  const store = ticketStore(client);
  /** The Idempotency-Key and the body of every trade request so far, as they went out. */
  const wire = () =>
    api.calls(TRADE).map((request) => ({
      key: request.headers.get("Idempotency-Key"),
      body: request.rawBody,
    }));
  return { api, client, store, wire };
}

/** Resolves once the request that is on its way has its answer. */
function answered(store: TicketStore, marketId = MARKET_ID): Promise<void> {
  return vi.waitFor(() => {
    expect(store.read(marketId).attempt?.sending ?? false).toBe(false);
  });
}

beforeEach(() => {
  // The portal's 401 reloads the page; a test stays where it is.
  vi.spyOn(page, "reload").mockImplementation(() => {});
});

describe("Confirm", () => {
  test("serialises the body once, gives it one key and sends exactly those", async () => {
    const answer = deferred();
    const { store, wire } = setup(answer.handler);

    store.confirm(MARKET_ID, buy);

    const { attempt } = store.read(MARKET_ID);
    expect(attempt).toMatchObject({ sending: true, error: null });
    expect(attempt?.submission.target).toBe(MARKET_ID);
    expect(attempt?.submission.body).toBe(
      '{"side":"BUY","outcome":"YES","amount_cr":"25","min_shares":"45.12","quote_version":7}',
    );
    expect(attempt?.submission.idempotencyKey).toMatch(UUID);

    await vi.waitFor(() => expect(wire()).toHaveLength(1));
    expect(wire()[0]).toEqual({
      key: attempt?.submission.idempotencyKey,
      body: attempt?.submission.body,
    });
  });

  test("a share-sized buy keeps the request's shares and is guarded by the quoted cost", async () => {
    const { store, api } = setup(deferred().handler);
    store.confirm(MARKET_ID, {
      ...buy,
      request: { side: "BUY", outcome: "YES", shares: "0025" },
      quote: quote({ shares: "25", amount_cr: "13.100001", quote_version: 9 }),
    });

    await vi.waitFor(() => expect(api.calls(TRADE)).toHaveLength(1));
    expect(api.lastCall(TRADE)?.rawBody).toBe(
      '{"side":"BUY","outcome":"YES","shares":"0025","max_cost_cr":"13.100001","quote_version":9}',
    );
  });

  test("a sell is guarded by the quote's proceeds", async () => {
    const { store, api } = setup(deferred().handler);

    store.confirm(MARKET_ID, sell);

    await vi.waitFor(() => expect(api.calls(TRADE)).toHaveLength(1));
    expect(api.lastCall(TRADE)?.rawBody).toBe(
      '{"side":"SELL","outcome":"YES","shares":"45.8225","min_proceeds_cr":"23.874512","quote_version":9}',
    );
  });

  test("sends every amount as the string it arrived as, digit for digit", async () => {
    const { store, api } = setup(deferred().handler);
    // More digits than a float keeps: any detour through a number would change them.
    const shares = "9007199254740993.000000000000000001";
    const proceeds = "0.100000000000000000000000000005";

    store.confirm(MARKET_ID, {
      ...sell,
      request: { side: "SELL", outcome: "YES", shares },
      quote: quote({ side: "SELL", shares, amount_cr: proceeds }),
    });

    await vi.waitFor(() => expect(api.calls(TRADE)).toHaveLength(1));
    expect(api.lastCall(TRADE)?.body).toMatchObject({ shares, min_proceeds_cr: proceeds });
    expect(api.lastCall(TRADE)?.rawBody).toContain(`"shares":"${shares}"`);
    expect(api.lastCall(TRADE)?.rawBody).toContain(`"min_proceeds_cr":"${proceeds}"`);
  });

  test("keeps what the ticket showed, so the trade can be shown unchanged while it waits", () => {
    const { store } = setup(deferred().handler);
    store.choose(MARKET_ID, { buyShares: "25" });

    store.confirm(MARKET_ID, buy);

    expect(store.read(MARKET_ID).attempt).toMatchObject({
      intent: buy.intent,
      request: buy.request,
      quote: buy.quote,
      buy: buy.buy,
      sellPresets: buy.sellPresets,
      choiceKey: "BUY:YES:25",
    });
  });

  test("a second tap while the first is on its way sends nothing", async () => {
    const { store, wire } = setup(deferred().handler);

    store.confirm(MARKET_ID, buy);
    const first = store.read(MARKET_ID).attempt;
    store.confirm(MARKET_ID, buy);
    store.confirm(MARKET_ID, sell);

    expect(store.read(MARKET_ID).attempt).toBe(first);
    // Give a second request every chance to go out.
    await vi.waitFor(() => expect(wire()).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(wire()).toHaveLength(1);
  });

  test("goes out at once even when the browser says it is offline, and fails at once", async () => {
    const { store, wire } = setup(noAnswer);
    onlineManager.setOnline(false);
    try {
      store.confirm(MARKET_ID, buy);
      await answered(store);

      // Not held back to be sent by itself later: it was tried, and it waits for a tap.
      expect(wire()).toHaveLength(1);
      expect(store.read(MARKET_ID).attempt?.error?.kind).toBe("network");
    } finally {
      onlineManager.setOnline(true);
    }
  });
});

/**
 * DESIGN.md §14: these outcomes say nothing about the trade, so the body and its key are
 * kept and "Try again" sends them again, unchanged.
 */
const NO_VERDICT: [name: string, answer: Handler, message: string][] = [
  [
    "a 503 with the API's code",
    apiError(503, "SERVICE_UNAVAILABLE", "This is temporarily unavailable. Try again shortly."),
    "This is temporarily unavailable. Try again shortly.",
  ],
  ["a 500", apiError(500, "INTERNAL_ERROR", "Something went wrong."), "Something went wrong."],
  [
    "an error page of the proxy",
    errorPage(502),
    "This is temporarily unavailable. Try again shortly.",
  ],
  [
    "a 504 with a body that is not JSON",
    errorPage(504),
    "This is temporarily unavailable. Try again shortly.",
  ],
  [
    "a 429",
    apiError(429, "RATE_LIMITED", "Too many attempts. Try again later.", {
      retry_after_seconds: 30,
    }),
    "Too many attempts. Try again later.",
  ],
  [
    "a 429 of the proxy, without a code",
    errorPage(429),
    "This is temporarily unavailable. Try again shortly.",
  ],
  ["the portal's 401", portal401(), "Your ETH sign-in has ended."],
  ["a request without a response", noAnswer, "No connection. Try again."],
];

describe.each(NO_VERDICT)("after %s", (_name, answer, message) => {
  test("the body and its key are kept, with the message to show", async () => {
    const { store } = setup(answer);

    store.confirm(MARKET_ID, buy);
    const sentAs = store.read(MARKET_ID).attempt?.submission;
    await answered(store);

    const ticket = store.read(MARKET_ID);
    expect(ticket.attempt?.submission).toBe(sentAs);
    expect(ticket.attempt?.error?.message).toBe(message);
    expect(ticket.refusal).toBeNull();
    expect(ticket.receipt).toBeNull();
  });

  test("Try again sends the same body with the same key, and nothing is sent without it", async () => {
    const { api, store, wire } = setup(answer);
    store.watch(MARKET_ID, () => {});

    store.confirm(MARKET_ID, buy);
    await answered(store);
    // Left alone, an unanswered trade stays unanswered.
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(wire()).toHaveLength(1);

    api.on(TRADE, json(201, tradeResponse()));
    store.retry(MARKET_ID);
    expect(store.read(MARKET_ID).attempt?.sending).toBe(true);
    await answered(store);

    const [first, replay] = wire();
    expect(wire()).toHaveLength(2);
    expect(replay?.key).toMatch(UUID);
    expect(replay).toEqual(first);
    // The 2xx is the verdict: the trade exists, and both are dropped.
    expect(store.read(MARKET_ID).attempt).toBeNull();
    expect(store.read(MARKET_ID).receipt).toEqual(tradeResponse());
  });

  test("Confirm cannot build another request while this one waits", async () => {
    const { store, wire } = setup(answer);

    store.confirm(MARKET_ID, buy);
    await answered(store);
    const waiting = store.read(MARKET_ID).attempt;

    store.confirm(MARKET_ID, { ...buy, quote: quote({ shares: "44", quote_version: 8 }) });
    store.choose(MARKET_ID, { buyShares: "50" });

    expect(store.read(MARKET_ID).attempt).toBe(waiting);
    expect(store.read(MARKET_ID).choice.buyShares).toBe("");
    expect(wire()).toHaveLength(1);
  });
});

describe("a trade that stays unanswered", () => {
  test("a network error, then a 429, then a 503 are all replays of one body with one key", async () => {
    const { api, store, wire } = setup(noAnswer);
    store.watch(MARKET_ID, () => {});

    store.confirm(MARKET_ID, buy);
    await answered(store);

    api.on(
      TRADE,
      apiError(429, "RATE_LIMITED", "Too many attempts. Try again later.", {
        retry_after_seconds: 2,
      }),
    );
    store.retry(MARKET_ID);
    await answered(store);
    expect(store.read(MARKET_ID).attempt?.error?.code).toBe("RATE_LIMITED");

    api.on(TRADE, apiError(503, "SERVICE_UNAVAILABLE", "This is temporarily unavailable."));
    store.retry(MARKET_ID);
    await answered(store);
    expect(store.read(MARKET_ID).attempt?.error?.code).toBe("SERVICE_UNAVAILABLE");

    api.on(TRADE, json(201, tradeResponse()));
    store.retry(MARKET_ID);
    await answered(store);

    const requests = wire();
    expect(requests).toHaveLength(4);
    expect(new Set(requests.map((request) => request.key)).size).toBe(1);
    expect(new Set(requests.map((request) => request.body)).size).toBe(1);
    expect(store.read(MARKET_ID).receipt).not.toBeNull();
  });

  test("two taps on Try again are one request", async () => {
    const { api, store, wire } = setup(noAnswer);
    store.confirm(MARKET_ID, buy);
    await answered(store);

    api.on(TRADE, deferred().handler);
    store.retry(MARKET_ID);
    store.retry(MARKET_ID);

    await vi.waitFor(() => expect(wire()).toHaveLength(2));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(wire()).toHaveLength(2);
  });

  test("Try again does nothing when no trade waits", async () => {
    const { store, wire } = setup(json(201, tradeResponse()));

    store.retry(MARKET_ID);

    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(wire()).toHaveLength(0);
  });

  test("a fault in handling the answer counts as no answer: the trade may exist", async () => {
    // A 201 whose body is not a trade: reading it fails after the request went through.
    const { api, store, wire } = setup(json(201, null));
    store.watch(MARKET_ID, () => {});

    store.confirm(MARKET_ID, buy);
    await answered(store);

    const { attempt } = store.read(MARKET_ID);
    expect(attempt?.error?.message).toBe(GENERIC_MESSAGE);
    expect(attempt?.error?.outcomeUnknown).toBe(true);

    api.on(TRADE, json(201, tradeResponse()));
    store.retry(MARKET_ID);
    await answered(store);
    expect(wire()[1]).toEqual(wire()[0]);
  });

  test("outlives the ticket: it is still there when nothing shows it", async () => {
    const { store } = setup(noAnswer);
    const stop = store.watch(MARKET_ID, () => {});
    store.confirm(MARKET_ID, buy);
    await answered(store);
    const waiting = store.read(MARKET_ID).attempt;

    stop();
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(store.read(MARKET_ID).attempt).toBe(waiting);
  });

  test("belongs to its market: another market's ticket is free", async () => {
    const { api, store } = setup(noAnswer);
    store.confirm(MARKET_ID, buy);
    await answered(store);

    api.on(TRADE, deferred().handler);
    store.confirm(OTHER_MARKET_ID, buy);

    expect(store.read(OTHER_MARKET_ID).attempt?.sending).toBe(true);
    expect(store.read(OTHER_MARKET_ID).attempt?.submission.idempotencyKey).not.toBe(
      store.read(MARKET_ID).attempt?.submission.idempotencyKey,
    );
    await vi.waitFor(() => expect(api.lastCall(TRADE)?.path).toContain(OTHER_MARKET_ID));
  });
});

/** DESIGN.md §14: a 4xx other than 429 is a verdict. The trade was refused, nothing was booked. */
const REFUSALS: [name: string, answer: Response][] = [
  ["409 QUOTE_STALE", apiError(409, "QUOTE_STALE", "The price moved.", { quote: quote() })],
  ["409 INSUFFICIENT_BALANCE", apiError(409, "INSUFFICIENT_BALANCE", "Not enough tokens.")],
  ["409 INSUFFICIENT_SHARES", apiError(409, "INSUFFICIENT_SHARES", "Not that many shares.")],
  ["409 LIMIT_EXCEEDED", apiError(409, "LIMIT_EXCEEDED", "This would exceed a limit.")],
  ["409 MARKET_NOT_OPEN", apiError(409, "MARKET_NOT_OPEN", "This market is not open.")],
  ["422 TRADE_TOO_SMALL", apiError(422, "TRADE_TOO_SMALL", "Below the minimum size.")],
  ["422 VALIDATION_ERROR", apiError(422, "VALIDATION_ERROR", "The request is not valid.")],
  ["422 IDEMPOTENCY_KEY_REUSED", apiError(422, "IDEMPOTENCY_KEY_REUSED", "Key already used.")],
  ["401 UNAUTHENTICATED", apiError(401, "UNAUTHENTICATED", "Sign in through the ETH portal.")],
  ["403 FORBIDDEN", apiError(403, "FORBIDDEN", "You are not allowed to do this.")],
  ["404 NOT_FOUND", apiError(404, "NOT_FOUND", "Not found.")],
];

describe.each(REFUSALS)("after a %s", (_name, answer) => {
  test("the body and its key are dropped, and the next Confirm has another key", async () => {
    const { store, wire } = setup(answer);
    store.choose(MARKET_ID, { buyShares: "25" });

    store.confirm(MARKET_ID, buy);
    await answered(store);

    const ticket = store.read(MARKET_ID);
    expect(ticket.attempt).toBeNull();
    expect(ticket.refusal?.error.status).toBe(answer.status);
    expect(ticket.refusal?.choiceKey).toBe("BUY:YES:25");
    // The form is as it was: the input is kept.
    expect(ticket.choice.buyShares).toBe("25");

    // Nothing waits, so Try again has nothing to send.
    store.retry(MARKET_ID);
    store.confirm(MARKET_ID, buy);
    await answered(store);

    const [first, second] = wire();
    expect(wire()).toHaveLength(2);
    expect(second?.key).toMatch(UUID);
    expect(second?.key).not.toBe(first?.key);
  });
});

describe("QUOTE_STALE", () => {
  const moved = quote({ shares: "44.871", average_price: 0.557, p_yes_after: 0.561 });
  const stale = apiError(
    409,
    "QUOTE_STALE",
    "The price moved. Review the new quote and confirm again.",
    {
      quote: { ...moved, quote_version: 8 },
    },
  );

  test("makes the quote of the answer the quote on screen for that input", async () => {
    const { client, store } = setup(stale);
    const quoteKey = keys.markets.quote(MARKET_ID, buy.request);
    // The sheet is open on this input: its quote is being read.
    const reading = new QueryObserver(client, { queryKey: quoteKey, enabled: false });
    const stop = reading.subscribe(() => {});
    client.setQueryData(quoteKey, buy.quote);

    store.confirm(MARKET_ID, buy);
    await answered(store);

    expect(client.getQueryData(quoteKey)).toEqual({ ...moved, quote_version: 8 });
    stop();
  });

  test("the next Confirm is another request: another body from that quote, another key", async () => {
    const { api, store, wire } = setup(stale);

    store.confirm(MARKET_ID, buy);
    await answered(store);
    // Nothing went out by itself.
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(wire()).toHaveLength(1);

    api.on(TRADE, deferred().handler);
    store.confirm(MARKET_ID, { ...buy, quote: { ...moved, quote_version: 8 } });
    await vi.waitFor(() => expect(wire()).toHaveLength(2));

    const [first, second] = wire();
    expect(second?.key).not.toBe(first?.key);
    expect(second?.body).toBe(
      '{"side":"BUY","outcome":"YES","amount_cr":"25","min_shares":"44.871","quote_version":8}',
    );
  });

  test("puts no quote in the cache when the sheet is closed: the next opening asks afresh", async () => {
    const { client, store } = setup(stale);

    store.confirm(MARKET_ID, buy);
    await answered(store);

    expect(client.getQueryState(keys.markets.quote(MARKET_ID, buy.request))).toBeUndefined();
  });
});

describe("a refusal that says the screen is behind", () => {
  /** The sheet is open on the input of `buy`: its quote is being read. */
  function reading(client: ReturnType<typeof setup>["client"]) {
    const quoteKey = keys.markets.quote(MARKET_ID, buy.request);
    const observer = new QueryObserver(client, { queryKey: quoteKey, enabled: false });
    const stop = observer.subscribe(() => {});
    client.setQueryData(quoteKey, buy.quote);
    return { quoteKey, stop };
  }

  test.each([
    ["INSUFFICIENT_BALANCE", "Not enough tokens for this."],
    ["INSUFFICIENT_SHARES", "You cannot sell more shares than you hold."],
    ["MARKET_NOT_OPEN", "This market is not open for trading."],
    ["LIMIT_EXCEEDED", "This would exceed a limit."],
  ] as const)(
    "%s marks the quote on screen out of date: it did not hold",
    async (code, message) => {
      const { client, store } = setup(apiError(409, code, message));
      const { quoteKey, stop } = reading(client);

      store.confirm(MARKET_ID, buy);
      await answered(store);

      expect(client.getQueryState(quoteKey)?.isInvalidated).toBe(true);
      stop();
    },
  );

  test.each([
    [422, "TRADE_TOO_SMALL"],
    [422, "VALIDATION_ERROR"],
    [422, "IDEMPOTENCY_KEY_REUSED"],
    [404, "NOT_FOUND"],
  ] as const)("a %i %s leaves the quote on screen as it is", async (status, code) => {
    const { client, store } = setup(apiError(status, code, "Refused."));
    const { quoteKey, stop } = reading(client);

    store.confirm(MARKET_ID, buy);
    await answered(store);

    expect(client.getQueryState(quoteKey)?.isInvalidated).toBe(false);
    expect(client.getQueryData(quoteKey)).toEqual(buy.quote);
    stop();
  });

  test("asks for no quote when the sheet is closed: the next opening asks afresh", async () => {
    const { client, store } = setup(apiError(409, "LIMIT_EXCEEDED", "This would exceed a limit."));

    store.confirm(MARKET_ID, buy);
    await answered(store);

    expect(client.getQueryState(keys.markets.quote(MARKET_ID, buy.request))).toBeUndefined();
  });
});

describe("the cache", () => {
  test("is updated by the trade's own handling: the balance and the stake are the answer's", async () => {
    const { client, store } = setup(
      json(201, tradeResponse({ balance_cr: "215", net_stake_cr: "25", stake_left_cr: "75" })),
    );
    client.setQueryData(keys.me, me({ balance_cr: "240" }));
    client.setQueryData(keys.markets.detail(MARKET_ID), marketDetail());

    store.confirm(MARKET_ID, buy);
    await answered(store);

    expect(client.getQueryData<MeResponse>(keys.me)?.balance_cr).toBe("215");
    const market = client.getQueryData<MarketDetailResponse>(keys.markets.detail(MARKET_ID));
    expect(market?.viewer).toMatchObject({ balance_cr: "215", stake_left_cr: "75" });
    expect(client.getQueryState(keys.markets.detail(MARKET_ID))?.isInvalidated).toBe(true);
  });

  test("an answer that says the caller is not who the app thinks makes the app ask who is calling", async () => {
    const { client, store } = setup(
      apiError(401, "UNAUTHENTICATED", "Sign in through the ETH portal to continue."),
    );
    client.setQueryData(keys.me, me());

    store.confirm(MARKET_ID, buy);
    await answered(store);

    // Start-up reads /me again and shows the full-screen message (DESIGN.md §14).
    expect(client.getQueryState(keys.me)?.isInvalidated).toBe(true);
    expect(store.read(MARKET_ID).refusal?.error.code).toBe("UNAUTHENTICATED");
  });

  test("a refusal that means the screen is behind marks the market out of date", async () => {
    const { client, store } = setup(
      apiError(409, "MARKET_NOT_OPEN", "This market is not open for trading."),
    );
    client.setQueryData(keys.markets.detail(MARKET_ID), marketDetail());

    store.confirm(MARKET_ID, buy);
    await answered(store);

    expect(client.getQueryState(keys.markets.detail(MARKET_ID))?.isInvalidated).toBe(true);
  });
});

describe("a 2xx", () => {
  test("is the receipt, and the form behind it is empty again", async () => {
    const { store } = setup(json(201, tradeResponse()));
    store.watch(MARKET_ID, () => {});
    store.choose(MARKET_ID, { buyShares: "25" });

    store.confirm(MARKET_ID, buy);
    await answered(store);

    expect(store.read(MARKET_ID)).toEqual({
      choice: { buyShares: "", shares: "" },
      attempt: null,
      refusal: null,
      receipt: tradeResponse(),
    });
  });

  test("Done forgets the receipt; while a receipt shows, nothing can be confirmed", async () => {
    const { store, wire } = setup(json(201, tradeResponse()));
    store.watch(MARKET_ID, () => {});
    store.confirm(MARKET_ID, buy);
    await answered(store);

    store.confirm(MARKET_ID, buy);
    store.choose(MARKET_ID, { buyShares: "50" });
    expect(store.read(MARKET_ID).choice.buyShares).toBe("");
    expect(wire()).toHaveLength(1);

    store.done(MARKET_ID);
    expect(store.read(MARKET_ID).receipt).toBeNull();
  });

  test("leaves nothing behind when the sheet was closed meanwhile", async () => {
    const { store } = setup(json(201, tradeResponse()));

    store.confirm(MARKET_ID, buy);
    await answered(store);

    expect(store.read(MARKET_ID)).toEqual({
      choice: { buyShares: "", shares: "" },
      attempt: null,
      refusal: null,
      receipt: null,
    });
  });

  test("Done does not drop a trade that waits", async () => {
    const { store } = setup(noAnswer);
    store.confirm(MARKET_ID, buy);
    await answered(store);
    const waiting = store.read(MARKET_ID).attempt;

    store.done(MARKET_ID);

    expect(store.read(MARKET_ID).attempt).toBe(waiting);
  });
});

describe("the form's state", () => {
  test("what is entered is kept per side, and changing the form drops the refusal", async () => {
    const { store } = setup(apiError(409, "INSUFFICIENT_BALANCE", "Not enough tokens for this."));
    store.choose(MARKET_ID, { buyShares: "25" });
    store.choose(MARKET_ID, { shares: "45.8225" });
    expect(store.read(MARKET_ID).choice).toEqual({ buyShares: "25", shares: "45.8225" });

    store.confirm(MARKET_ID, buy);
    await answered(store);
    expect(store.read(MARKET_ID).refusal).not.toBeNull();

    // A refusal is about what was sent.
    store.choose(MARKET_ID);
    expect(store.read(MARKET_ID).refusal).toBeNull();
    expect(store.read(MARKET_ID).choice).toEqual({ buyShares: "25", shares: "45.8225" });
  });

  test("tells its watchers of every change, and hands out the same object in between", () => {
    const { store } = setup(deferred().handler);
    const onChange = vi.fn();
    store.watch(MARKET_ID, onChange);
    const before = store.read(MARKET_ID);
    expect(store.read(MARKET_ID)).toBe(before);

    store.choose(MARKET_ID, { buyShares: "50" });

    expect(onChange).toHaveBeenCalledTimes(1);
    expect(store.read(MARKET_ID)).not.toBe(before);
    expect(store.read(MARKET_ID)).toBe(store.read(MARKET_ID));
    expect(store.read(OTHER_MARKET_ID)).toBe(before);
  });

  test("is forgotten once the ticket has left the screen", async () => {
    const { store } = setup(deferred().handler);
    const stop = store.watch(MARKET_ID, () => {});
    store.choose(MARKET_ID, { buyShares: "50" });

    stop();
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(store.read(MARKET_ID).choice.buyShares).toBe("");
  });

  test("is kept when the ticket is back within the same breath: the sheet became a dialog", async () => {
    const { store } = setup(deferred().handler);
    const stop = store.watch(MARKET_ID, () => {});
    store.choose(MARKET_ID, { buyShares: "50" });

    stop();
    store.watch(MARKET_ID, () => {});
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(store.read(MARKET_ID).choice.buyShares).toBe("50");
  });

  test("lives with its query client: another client starts empty", async () => {
    const { store } = setup(noAnswer);
    store.confirm(MARKET_ID, buy);
    await answered(store);

    const elsewhere = createTestQueryClient();

    expect(ticketStore(elsewhere)).not.toBe(store);
    expect(ticketStore(elsewhere)).toBe(ticketStore(elsewhere));
    expect(ticketStore(elsewhere).read(MARKET_ID).attempt).toBeNull();
  });
});

describe("choiceKey", () => {
  test("names the side, the outcome and what is entered for that side", () => {
    const choice = { buyShares: "25", shares: "91.645" } as const;

    expect(choiceKey({ side: "BUY", outcome: "NO" }, choice)).toBe("BUY:NO:25");
    expect(choiceKey({ side: "SELL", outcome: "YES" }, choice)).toBe("SELL:YES:91.645");
    expect(choiceKey({ side: "SELL", outcome: "YES" }, { buyShares: "25", shares: "" })).toBe(
      "SELL:YES:",
    );
  });

  test("an amount is named by its characters: two texts for one figure are two choices", () => {
    const buying = { side: "BUY", outcome: "YES" } as const;

    expect(choiceKey(buying, { buyShares: "37.5", shares: "" })).toBe("BUY:YES:37.5");
    expect(choiceKey(buying, { buyShares: "37.50", shares: "" })).toBe("BUY:YES:37.50");
    expect(choiceKey(buying, { buyShares: "", shares: "" })).toBe("BUY:YES:");
  });
});

describe("the amount of a buy", () => {
  test("is the text that was entered, kept as it is", () => {
    const { store } = setup(deferred().handler);
    store.watch(MARKET_ID, () => {});
    // More digits than a float keeps, and zeros a number would drop.
    const typed = "007.100000000000000000000000000001";

    store.choose(MARKET_ID, { buyShares: typed });

    expect(store.read(MARKET_ID).choice.buyShares).toBe(typed);
  });

  test("goes into the body exactly as the quote was asked for it", async () => {
    const { store, wire } = setup(deferred().handler);
    const typed = "0.100000000000000000000000000001";

    store.confirm(MARKET_ID, {
      ...buy,
      request: { side: "BUY", outcome: "YES", amount_cr: typed },
      quote: quote({ amount_cr: "0.1", shares: "0.18", quote_version: 12 }),
    });

    await vi.waitFor(() => expect(wire()).toHaveLength(1));
    // The amount is what was typed, not the amount the quote answered with.
    expect(wire()[0]?.body).toBe(
      `{"side":"BUY","outcome":"YES","amount_cr":"${typed}","min_shares":"0.18","quote_version":12}`,
    );
  });

  test("a refusal is about the amount that was sent: it is named by that text", async () => {
    const { store } = setup(apiError(409, "LIMIT_EXCEEDED", "This would exceed a limit."));
    store.watch(MARKET_ID, () => {});
    store.choose(MARKET_ID, { buyShares: "37.5" });

    store.confirm(MARKET_ID, {
      ...buy,
      request: { side: "BUY", outcome: "YES", amount_cr: "37.5" },
    });
    await answered(store);

    expect(store.read(MARKET_ID).refusal?.choiceKey).toBe("BUY:YES:37.5");
    // Another amount is another choice: the refusal goes with the change.
    store.choose(MARKET_ID, { buyShares: "37" });
    expect(store.read(MARKET_ID).refusal).toBeNull();
    expect(store.read(MARKET_ID).choice.buyShares).toBe("37");
  });

  test("cannot be changed while a trade waits: what was sent stays what is shown", async () => {
    const { store } = setup(noAnswer);
    store.watch(MARKET_ID, () => {});
    store.choose(MARKET_ID, { buyShares: "25" });
    store.confirm(MARKET_ID, buy);
    await answered(store);

    store.choose(MARKET_ID, { buyShares: "100" });

    expect(store.read(MARKET_ID).choice.buyShares).toBe("25");
    expect(store.read(MARKET_ID).attempt?.request).toEqual(buy.request);
  });
});
