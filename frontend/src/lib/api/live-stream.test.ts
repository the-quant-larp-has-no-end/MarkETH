import {
  MutationObserver,
  type MutationObserverOptions,
  QueryClient,
  type QueryKey,
  QueryObserver,
} from "@tanstack/react-query";
import { describe, expect, test } from "vitest";

import {
  adminBroadcasts,
  BROADCAST_ID,
  broadcastIn,
  broadcastViewer,
  chat,
  CHAT_MESSAGE_ID,
  chatMessage,
  chatMessageEvent,
  chatMessageRemoved,
  closedChat,
  config,
  liveBroadcasts,
  MARKET_ID,
  marketBroadcast,
  noBroadcast,
  OTHER_CHAT_MESSAGE_ID,
  OTHER_MARKET_ID,
} from "@/test/fixtures";
import { apiError, deferred, errorPage, json, mockApi, noAnswer, type Route } from "@/test/http";

import { dropChatMessage, putChatMessage } from "./chat-cache";
import { call } from "./client";
import type { ApiError } from "./errors";
import { keys } from "./keys";
import {
  approveBroadcastMutation,
  endBroadcastMutation,
  postChatMessageMutation,
  refuseBroadcastMutation,
  removeChatMessageMutation,
  requestBroadcastMutation,
  startBroadcastAsAdminMutation,
  startBroadcastMutation,
} from "./mutations";
import {
  adminBroadcastsQuery,
  chatQuery,
  liveBroadcastsQuery,
  marketBroadcastQuery,
} from "./queries";
import type { ChatResponse, MarketBroadcastResponse } from "./schema";

/**
 * The reads and the writes of a live stream and of its chat (`keys.ts`, `queries.ts`,
 * `mutations.ts`, `chat-cache.ts`): what each sends, and what each does to the cache.
 */

const BROADCAST = keys.markets.broadcast(MARKET_ID);
const CHAT = keys.markets.chat(MARKET_ID);
const LIVE = keys.broadcasts.live;
const ADMIN = keys.admin.broadcasts;

/** A cache that holds every read a write of the feature could touch, for two markets. */
function seeded(): QueryClient {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(keys.config, config());
  client.setQueryData(BROADCAST, noBroadcast({ can_request: true }));
  client.setQueryData(keys.markets.broadcast(OTHER_MARKET_ID), noBroadcast());
  client.setQueryData(CHAT, chat());
  client.setQueryData(keys.markets.chat(OTHER_MARKET_ID), closedChat());
  client.setQueryData(LIVE, liveBroadcasts());
  client.setQueryData(ADMIN, adminBroadcasts());
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

/** The two reads a write waits for after no answer, asked as the app asks them. */
const readBroadcast = () =>
  call((api) =>
    api.GET("/api/markets/{market_id}/broadcast", { params: { path: { market_id: MARKET_ID } } }),
  );
const readChat = () =>
  call((api) =>
    api.GET("/api/markets/{market_id}/chat", { params: { path: { market_id: MARKET_ID } } }),
  );

const texts = (client: QueryClient, key: QueryKey = CHAT) =>
  client.getQueryData<ChatResponse>(key)?.items.map((item) => item.text);

describe("the reads of a live stream and of its chat", () => {
  test("each asks its own path and comes back typed and unchanged", async () => {
    const api = mockApi({
      "GET /api/markets/{market_id}/broadcast": json(200, marketBroadcast()),
      "GET /api/broadcasts/live": json(200, liveBroadcasts()),
      "GET /api/admin/broadcasts": json(200, adminBroadcasts()),
      "GET /api/markets/{market_id}/chat": json(200, chat()),
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    const broadcast = await client.fetchQuery(marketBroadcastQuery(MARKET_ID));
    const live = await client.fetchQuery(liveBroadcastsQuery);
    const admin = await client.fetchQuery(adminBroadcastsQuery);
    const messages = await client.fetchQuery(chatQuery(MARKET_ID));

    expect(api.requests.map((request) => request.path)).toEqual([
      `/api/markets/${MARKET_ID}/broadcast`,
      "/api/broadcasts/live",
      "/api/admin/broadcasts",
      `/api/markets/${MARKET_ID}/chat`,
    ]);
    expect(api.requests.every((request) => request.query.toString() === "")).toBe(true);
    expect(broadcast).toEqual(marketBroadcast());
    expect(broadcast.broadcast?.playlist_url).toBe(
      `/api/broadcasts/${BROADCAST_ID}/hls/index.m3u8`,
    );
    expect(live.items[0]?.market_id).toBe(MARKET_ID);
    expect(admin.items[0]?.requested_by?.username).toBe("cab_coffee");
    expect(messages.items.map((item) => item.from_vis)).toEqual([false, true]);
    // Each under its key: what a write and an event mark out of date.
    expect(client.getQueryData(BROADCAST)).toEqual(broadcast);
    expect(client.getQueryData(LIVE)).toEqual(live);
    expect(client.getQueryData(ADMIN)).toEqual(admin);
    expect(client.getQueryData(CHAT)).toEqual(messages);
  });

  test("a market the caller may not see is the API's NOT_FOUND, for the section to be absent", async () => {
    mockApi({
      "GET /api/markets/{market_id}/broadcast": apiError(404, "NOT_FOUND", "Not found."),
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    await expect(client.fetchQuery(marketBroadcastQuery(MARKET_ID))).rejects.toMatchObject({
      code: "NOT_FOUND",
      message: "Not found.",
    });
  });

  test("the keys of one market stand under the market reads, apart from each other and from its detail", () => {
    expect(BROADCAST).toEqual(["markets", "broadcast", MARKET_ID]);
    expect(CHAT).toEqual(["markets", "chat", MARKET_ID]);
    expect(LIVE).toEqual(["broadcasts", "live"]);
    expect(ADMIN).toEqual(["admin", "broadcasts"]);
  });
});

describe("the six writes of a live stream", () => {
  const live = marketBroadcast({
    viewer: broadcastViewer({ is_broadcaster: true, can_end: true }),
  });
  const ids = { marketId: MARKET_ID, broadcastId: BROADCAST_ID };

  /** Each write: its route, the body it sends, and whether it opens or closes the chat. */
  const WRITES = [
    {
      name: "request",
      route: "POST /api/markets/{market_id}/broadcast/request",
      path: `/api/markets/${MARKET_ID}/broadcast/request`,
      send: (client: QueryClient) =>
        run(client, requestBroadcastMutation, { marketId: MARKET_ID, policy_accepted: true }),
      body: { policy_accepted: true },
      answer: marketBroadcast({
        broadcast: broadcastIn("REQUESTED"),
        viewer: broadcastViewer({ is_broadcaster: true, can_watch: false }),
      }),
      status: 201,
      chat: false,
    },
    {
      name: "start",
      route: "POST /api/broadcasts/{broadcast_id}/start",
      path: `/api/broadcasts/${BROADCAST_ID}/start`,
      send: (client: QueryClient) => run(client, startBroadcastMutation, ids),
      body: undefined,
      answer: live,
      status: 200,
      chat: true,
    },
    {
      name: "start as an admin",
      route: "POST /api/admin/markets/{market_id}/broadcast",
      path: `/api/admin/markets/${MARKET_ID}/broadcast`,
      send: (client: QueryClient) =>
        run(client, startBroadcastAsAdminMutation, { marketId: MARKET_ID }),
      body: undefined,
      answer: live,
      status: 201,
      chat: true,
    },
    {
      name: "end",
      route: "POST /api/broadcasts/{broadcast_id}/end",
      path: `/api/broadcasts/${BROADCAST_ID}/end`,
      send: (client: QueryClient) => run(client, endBroadcastMutation, ids),
      body: undefined,
      answer: marketBroadcast({
        broadcast: broadcastIn("ENDED"),
        viewer: broadcastViewer({ is_broadcaster: true, can_watch: false }),
      }),
      status: 200,
      chat: true,
    },
    {
      name: "approve",
      route: "POST /api/admin/broadcasts/{broadcast_id}/approve",
      path: `/api/admin/broadcasts/${BROADCAST_ID}/approve`,
      send: (client: QueryClient) => run(client, approveBroadcastMutation, ids),
      body: undefined,
      answer: marketBroadcast({
        broadcast: broadcastIn("APPROVED"),
        viewer: broadcastViewer({ can_refuse: true, can_watch: false }),
      }),
      status: 200,
      chat: false,
    },
    {
      name: "refuse",
      route: "POST /api/admin/broadcasts/{broadcast_id}/refuse",
      path: `/api/admin/broadcasts/${BROADCAST_ID}/refuse`,
      send: (client: QueryClient) =>
        run(client, refuseBroadcastMutation, { ...ids, reason: "The event is over." }),
      body: { reason: "The event is over." },
      answer: marketBroadcast({
        broadcast: broadcastIn("REFUSED", { refusal_reason: "The event is over." }),
        viewer: broadcastViewer({ can_start_as_admin: true, can_watch: false }),
      }),
      status: 200,
      chat: false,
    },
  ] as const satisfies readonly {
    name: string;
    route: Route;
    path: string;
    send: (client: QueryClient) => Promise<MarketBroadcastResponse>;
    body: unknown;
    answer: MarketBroadcastResponse;
    status: number;
    chat: boolean;
  }[];

  test.each(WRITES)(
    "$name: the answer is the live stream of the market, and the two lists are read again",
    async ({ route, path, send, body, answer, status, chat: touchesChat }) => {
      const api = mockApi({ [route]: json(status, answer) });
      const client = seeded();

      const returned = await send(client);

      const sent = api.lastCall(route);
      expect(api.requests).toHaveLength(1);
      expect(sent?.path).toBe(path);
      expect(sent?.body).toEqual(body);
      expect(returned).toEqual(answer);
      expect(client.getQueryData(BROADCAST)).toEqual(answer);
      expect(stale(client, BROADCAST)).toBe(false);
      expect(stale(client, LIVE)).toBe(true);
      expect(stale(client, ADMIN)).toBe(true);
      // The chat opens with a start and closes with an end, and with nothing else.
      expect(stale(client, CHAT)).toBe(touchesChat);
      // Another market is another live stream and another chat.
      expect(stale(client, keys.markets.broadcast(OTHER_MARKET_ID))).toBe(false);
      expect(stale(client, keys.markets.chat(OTHER_MARKET_ID))).toBe(false);
    },
  );

  test.each(WRITES)(
    "$name: a refusal that says the screen is behind reads the live stream again",
    async ({ route, send }) => {
      mockApi({ [route]: apiError(409, "CONFLICT", "This request has been decided already.") });
      const client = seeded();
      const before = client.getQueryData(BROADCAST);

      await expect(send(client)).rejects.toMatchObject({ code: "CONFLICT" });

      expect(client.getQueryData(BROADCAST)).toEqual(before);
      expect(stale(client, BROADCAST)).toBe(true);
      expect(stale(client, LIVE)).toBe(true);
      expect(stale(client, ADMIN)).toBe(true);
    },
  );

  test.each(WRITES)(
    "$name: a field that was refused changes nothing in the cache",
    async ({ route, send }) => {
      mockApi({
        [route]: apiError(422, "VALIDATION_ERROR", "This cannot be empty.", { field: "reason" }),
      });
      const client = seeded();

      await expect(send(client)).rejects.toMatchObject({ code: "VALIDATION_ERROR" });

      expect([BROADCAST, LIVE, ADMIN, CHAT].some((key) => stale(client, key))).toBe(false);
    },
  );

  test.each(WRITES)(
    "$name: after no answer it stays pending until the live stream was read again",
    async ({ route, send }) => {
      const read = deferred();
      const api = mockApi({
        [route]: () => noAnswer(),
        "GET /api/markets/{market_id}/broadcast": read.handler,
      });
      const client = seeded();
      const stop = onScreen(client, BROADCAST, readBroadcast);
      let settled = false;

      const sending = send(client).catch((error: ApiError) => {
        settled = true;
        return error;
      });
      await expect
        .poll(() => api.calls("GET /api/markets/{market_id}/broadcast").length)
        .toBeGreaterThan(0);
      expect(settled).toBe(false);
      read.resolve(json(200, live));

      expect(await sending).toMatchObject({ kind: "network", outcomeUnknown: true });
      expect(client.getQueryData(BROADCAST)).toEqual(live);
      stop();
    },
  );

  test("an error page of the proxy is no answer either", async () => {
    mockApi({ "POST /api/broadcasts/{broadcast_id}/end": errorPage(502) });
    const client = seeded();

    await expect(run(client, endBroadcastMutation, ids)).rejects.toMatchObject({
      outcomeUnknown: true,
    });

    expect([BROADCAST, LIVE, ADMIN, CHAT].every((key) => stale(client, key))).toBe(true);
  });
});

describe("a chat message that is posted", () => {
  const POST: Route = "POST /api/markets/{market_id}/chat";
  const mine = chatMessage({
    id: "0b1c2d3e-4f5a-4b6c-8d7e-9f0a1b2c3d4e",
    username: "anna_m",
    text: "Here it comes",
  });

  test("goes out with the characters that were typed and joins the chat at its end", async () => {
    const api = mockApi({ [POST]: json(201, mine) });
    const client = seeded();

    const answer = await run(client, postChatMessageMutation, {
      marketId: MARKET_ID,
      text: "  Here it comes ",
    });

    expect(api.lastCall(POST)?.path).toBe(`/api/markets/${MARKET_ID}/chat`);
    expect(api.lastCall(POST)?.rawBody).toBe('{"text":"  Here it comes "}');
    expect(answer).toEqual(mine);
    expect(texts(client)).toEqual([
      "Is that the upper station?",
      "Yes, and the cabin leaves in a minute.",
      "Here it comes",
    ]);
    expect(stale(client, CHAT)).toBe(false);
    expect(texts(client, keys.markets.chat(OTHER_MARKET_ID))).toEqual([]);
  });

  test("stands there once, whichever arrives first: its answer or its event", async () => {
    mockApi({ [POST]: json(201, mine) });
    const client = seeded();
    const event = chatMessageEvent({ message: mine });

    // The event first, then the answer.
    expect(putChatMessage(client, MARKET_ID, event.message, event.broadcast_id)).toBe(true);
    await run(client, postChatMessageMutation, { marketId: MARKET_ID, text: "Here it comes" });
    // And the event once more.
    expect(putChatMessage(client, MARKET_ID, event.message, event.broadcast_id)).toBe(true);

    expect(texts(client)?.filter((text) => text === "Here it comes")).toHaveLength(1);
  });

  test("into a chat the cache holds as closed makes the chat be read: the cache is behind", async () => {
    mockApi({ [POST]: json(201, mine) });
    const client = seeded();
    client.setQueryData(CHAT, closedChat());

    await run(client, postChatMessageMutation, { marketId: MARKET_ID, text: "Here it comes" });

    expect(texts(client)).toEqual([]);
    expect(stale(client, CHAT)).toBe(true);
  });

  test("and is refused because the chat is closed: the chat and the live stream are read again", async () => {
    mockApi({ [POST]: apiError(409, "CONFLICT", "The live chat is closed.") });
    const client = seeded();

    await expect(
      run(client, postChatMessageMutation, { marketId: MARKET_ID, text: "Hello" }),
    ).rejects.toMatchObject({ message: "The live chat is closed." });

    expect(stale(client, CHAT)).toBe(true);
    expect(stale(client, BROADCAST)).toBe(true);
  });

  test("and is told to wait changes nothing: the composer shows the wait", async () => {
    mockApi({
      [POST]: apiError(429, "RATE_LIMITED", "The chat is busy. Try again in a moment.", {
        retry_after_seconds: 2,
      }),
    });
    const client = seeded();

    await expect(
      run(client, postChatMessageMutation, { marketId: MARKET_ID, text: "Hello" }),
    ).rejects.toMatchObject({ code: "RATE_LIMITED" });

    expect(stale(client, CHAT)).toBe(false);
    expect(texts(client)).toHaveLength(2);
  });

  test("and gets no answer stays pending until the chat was read again", async () => {
    const read = deferred();
    const api = mockApi({
      [POST]: () => noAnswer(),
      "GET /api/markets/{market_id}/chat": read.handler,
    });
    const client = seeded();
    const stop = onScreen(client, CHAT, readChat);
    let settled = false;

    const sending = run(client, postChatMessageMutation, {
      marketId: MARKET_ID,
      text: "Hello",
    }).catch((error: ApiError) => {
      settled = true;
      return error;
    });
    await expect
      .poll(() => api.calls("GET /api/markets/{market_id}/chat").length)
      .toBeGreaterThan(0);
    expect(settled).toBe(false);
    // The message was stored after all: the read shows it.
    read.resolve(json(200, chat({ items: [...chat().items, mine] })));

    expect(await sending).toMatchObject({ outcomeUnknown: true });
    expect(texts(client)?.at(-1)).toBe("Here it comes");
    stop();
  });
});

describe("a chat message an admin removes", () => {
  const REMOVE: Route = "POST /api/admin/markets/{market_id}/chat/{message_id}/remove";
  const variables = { marketId: MARKET_ID, messageId: CHAT_MESSAGE_ID };

  test("leaves the chat at once", async () => {
    const api = mockApi({ [REMOVE]: json(200, chatMessageRemoved()) });
    const client = seeded();

    await run(client, removeChatMessageMutation, variables);

    expect(api.lastCall(REMOVE)?.path).toBe(
      `/api/admin/markets/${MARKET_ID}/chat/${CHAT_MESSAGE_ID}/remove`,
    );
    expect(api.lastCall(REMOVE)?.body).toBeUndefined();
    expect(texts(client)).toEqual(["Yes, and the cabin leaves in a minute."]);
    expect(stale(client, CHAT)).toBe(false);
  });

  test("that is gone already has the chat read again", async () => {
    mockApi({ [REMOVE]: apiError(404, "NOT_FOUND", "Not found.") });
    const client = seeded();

    await expect(run(client, removeChatMessageMutation, variables)).rejects.toMatchObject({
      code: "NOT_FOUND",
    });

    expect(stale(client, CHAT)).toBe(true);
  });

  test("without an answer has the chat read again before anything else is offered", async () => {
    mockApi({ [REMOVE]: errorPage(502) });
    const client = seeded();

    await expect(run(client, removeChatMessageMutation, variables)).rejects.toMatchObject({
      outcomeUnknown: true,
    });

    expect(stale(client, CHAT)).toBe(true);
  });
});

describe("what a message does to the cached chat", () => {
  const event = chatMessageEvent();

  test("a message of the live stream on screen joins at the end", () => {
    const client = seeded();

    expect(putChatMessage(client, MARKET_ID, event.message, event.broadcast_id)).toBe(true);

    expect(texts(client)?.at(-1)).toBe("There it goes.");
    expect(client.getQueryData<ChatResponse>(CHAT)).toMatchObject({
      open: true,
      broadcast_id: BROADCAST_ID,
      can_remove: false,
    });
  });

  test("a message of another live stream, of a closed chat or of a chat nobody read is dropped", () => {
    const client = seeded();

    expect(putChatMessage(client, MARKET_ID, event.message, "another-live-stream")).toBe(false);
    expect(putChatMessage(client, OTHER_MARKET_ID, event.message, event.broadcast_id)).toBe(false);
    client.removeQueries({ queryKey: CHAT });
    expect(putChatMessage(client, MARKET_ID, event.message, event.broadcast_id)).toBe(false);

    expect(client.getQueryData(CHAT)).toBeUndefined();
    expect(texts(client, keys.markets.chat(OTHER_MARKET_ID))).toEqual([]);
  });

  test("the list keeps its latest messages, as many as /config says", () => {
    const client = seeded();
    client.setQueryData(keys.config, config({ chat_backlog_size: 2 }));

    putChatMessage(client, MARKET_ID, event.message, event.broadcast_id);

    expect(texts(client)).toEqual(["Yes, and the cabin leaves in a minute.", "There it goes."]);
  });

  test("a removed message leaves, and one that is not there changes nothing", () => {
    const client = seeded();
    const before = client.getQueryData(CHAT);

    dropChatMessage(client, MARKET_ID, "no-such-message");
    expect(client.getQueryData(CHAT)).toBe(before);
    dropChatMessage(client, OTHER_MARKET_ID, CHAT_MESSAGE_ID);
    expect(client.getQueryData(CHAT)).toBe(before);

    dropChatMessage(client, MARKET_ID, OTHER_CHAT_MESSAGE_ID);
    expect(texts(client)).toEqual(["Is that the upper station?"]);
  });
});

describe("what each write is given", () => {
  test("a write without its live stream, its tick, its reason or its market does not compile", () => {
    const client = seeded();
    // Never called: the compiler is the test, and `tsc` fails if one of these lines passes.
    const refused = () => {
      // @ts-expect-error a start names the live stream beside its market
      void run(client, startBroadcastMutation, { marketId: MARKET_ID });
      // @ts-expect-error a request states that the usage policy is accepted
      void run(client, requestBroadcastMutation, { marketId: MARKET_ID });
      // @ts-expect-error and states it as true
      void run(client, requestBroadcastMutation, { marketId: MARKET_ID, policy_accepted: false });
      // @ts-expect-error a refusal has its reason
      void run(client, refuseBroadcastMutation, { marketId: MARKET_ID, broadcastId: BROADCAST_ID });
      // @ts-expect-error a chat message names its market
      void run(client, postChatMessageMutation, { text: "Hello" });
      // @ts-expect-error a removal names the message
      void run(client, removeChatMessageMutation, { marketId: MARKET_ID });
    };

    expect(refused).toBeTypeOf("function");
  });
});
