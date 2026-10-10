import { vi } from "vitest";

import type { ErrorCode, paths } from "@/lib/api/schema";

/**
 * The API, answered by the test. `mockApi` stands in for `fetch`, the one door every call
 * of the app goes through, so everything above it (the client, the error handling, the
 * cache) runs as it does in the browser.
 *
 *     const api = mockApi({
 *       "GET /api/markets": json(200, { items: [marketSummary()] }),
 *       "POST /api/codes/redeem": apiError(422, "CODE_INVALID", "This code is not valid."),
 *     });
 *     ...
 *     expect(api.lastCall("POST /api/codes/redeem")?.body).toEqual({ code: "ABCD-EFGH-JKMN" });
 *
 * A route is a method and a path of `backend/openapi.json`, so a typo does not compile.
 * `{market_id}` in a route matches any id. A request no route answers fails the test, and
 * so does one of a test that never calls `mockApi`: the set-up starts every test with no
 * route at all (`src/test/setup.ts`), so nothing a test asks ever leaves for the network.
 */

type Method = "get" | "put" | "post" | "delete" | "patch";

/** Every operation of the contract, as `"GET /api/markets/{market_id}"`. */
export type Route = {
  [Path in keyof paths]: {
    [M in Method]: paths[Path][M] extends object ? `${Uppercase<M>} ${Path & string}` : never;
  }[Method];
}[keyof paths];

/** A request the app made, as the test sees it. */
export type MockRequest = {
  method: string;
  /** The whole address the request went to. */
  url: string;
  /** The path without its query, e.g. `/api/markets/3f6c…`. */
  path: string;
  query: URLSearchParams;
  headers: Headers;
  /** The parsed JSON body, or undefined when there was none. */
  body: unknown;
  /** The body exactly as it was sent. */
  rawBody: string;
};

type Reply = Response | Promise<Response>;
/** What a route answers: one response, or a function of the request. */
export type Handler = Response | ((request: MockRequest) => Reply);

export type ApiMock = {
  /** Every request so far, in order. */
  readonly requests: readonly MockRequest[];
  /** The requests one route received. */
  calls(route: Route): MockRequest[];
  /** The latest request one route received. */
  lastCall(route: Route): MockRequest | undefined;
  /** Adds a route or replaces its answer, for the rest of the test. */
  on(route: Route, handler: Handler): void;
};

/** A JSON answer. */
export function json(
  status: number,
  body: unknown,
  headers: Record<string, string> = {},
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

/** An error of the API: `{code, message, details}`. */
export function apiError(
  status: number,
  code: ErrorCode,
  message: string,
  details: Record<string, unknown> = {},
): Response {
  return json(status, { code, message, details });
}

/** What the portal answers a call it has no session for: a 401 with the body `{}`. */
export function portal401(): Response {
  return json(401, {});
}

/** An error page of the proxy: not JSON, no `code`. */
export function errorPage(status = 502): Response {
  return new Response("<html><body>Bad Gateway</body></html>", {
    status,
    headers: { "Content-Type": "text/html" },
  });
}

/** A request that gets no response at all. Use it as a handler. */
export function noAnswer(): Promise<Response> {
  return Promise.reject(new TypeError("Failed to fetch"));
}

/**
 * An answer the test gives later, to look at what the screen shows while it waits.
 *
 *     const answer = deferred();
 *     mockApi({ "POST /api/vouchers": answer.handler });
 *     ...
 *     answer.resolve(json(201, voucherIssued()));
 */
export function deferred(): {
  handler: () => Promise<Response>;
  resolve(response: Response): void;
} {
  let settle: (response: Response) => void = () => {};
  const promise = new Promise<Response>((resolve) => {
    settle = resolve;
  });
  return { handler: () => promise, resolve: (response) => settle(response) };
}

type Entry = { method: string; pattern: RegExp; exact: boolean; handler: Handler };

const unanswered: string[] = [];

function entry(route: string, handler: Handler): Entry {
  const space = route.indexOf(" ");
  const method = route.slice(0, space);
  const path = route.slice(space + 1);
  const pattern = path
    .split(/(\{[^}]+\})/)
    .map((part) => (part.startsWith("{") ? "[^/]+" : part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")))
    .join("");
  return { method, pattern: new RegExp(`^${pattern}$`), exact: !path.includes("{"), handler };
}

export function mockApi(routes: Partial<Record<Route, Handler>> = {}): ApiMock {
  const entries = new Map<string, Entry>();
  const requests: MockRequest[] = [];
  for (const [route, handler] of Object.entries(routes)) {
    if (handler !== undefined) entries.set(route, entry(route, handler));
  }

  const find = (method: string, path: string): Entry | undefined => {
    const matching = [...entries.values()].filter(
      (candidate) => candidate.method === method && candidate.pattern.test(path),
    );
    return matching.find((candidate) => candidate.exact) ?? matching[0];
  };

  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const sent = input instanceof Request ? input : new Request(input, init);
    const url = new URL(sent.url);
    const rawBody = await sent.clone().text();
    let body: unknown;
    try {
      body = rawBody === "" ? undefined : JSON.parse(rawBody);
    } catch {
      body = rawBody;
    }
    const request: MockRequest = {
      method: sent.method,
      url: sent.url,
      path: url.pathname,
      query: url.searchParams,
      headers: sent.headers,
      body,
      rawBody,
    };
    requests.push(request);

    const found = find(request.method, request.path);
    if (found === undefined) {
      const missing = `${request.method} ${request.path}`;
      unanswered.push(missing);
      throw new TypeError(`mockApi has no route for ${missing}`);
    }
    const reply =
      typeof found.handler === "function" ? await found.handler(request) : found.handler;
    // A Response can be read once; a route may be called many times.
    return reply.clone();
  });

  const matches = (route: Route) => {
    const wanted = entry(route, new Response());
    return requests.filter(
      (request) => request.method === wanted.method && wanted.pattern.test(request.path),
    );
  };

  return {
    requests,
    calls: matches,
    lastCall: (route) => matches(route).at(-1),
    on: (route, handler) => {
      entries.set(route, entry(route, handler));
    },
  };
}

/** Run after every test (`src/test/setup.ts`): a request without a route is a failed test. */
export function verifyApiMock(): void {
  if (unanswered.length === 0) return;
  const missing = [...new Set(unanswered)].join(", ");
  unanswered.length = 0;
  throw new Error(`The test made API requests that mockApi has no route for: ${missing}`);
}
