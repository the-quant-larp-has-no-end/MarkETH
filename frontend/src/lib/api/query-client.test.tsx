import { MutationObserver, QueryClientProvider, QueryObserver } from "@tanstack/react-query";
import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { config, me } from "@/test/fixtures";
import { apiError, errorPage, type Handler, json, mockApi, noAnswer } from "@/test/http";
import { createTestQueryClient } from "@/test/render";

import { keys } from "./keys";
import { redeemCodeMutation } from "./mutations";
import { marketsQuery, meQuery, positionsQuery } from "./queries";
import { createQueryClient } from "./query-client";
import { useConfig, useMe } from "./session";

describe("a read that failed", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  async function read(handler: Handler) {
    const api = mockApi({ "GET /api/me/positions": handler });
    const client = createQueryClient();
    const observer = new QueryObserver(client, positionsQuery);
    const stop = observer.subscribe(() => {});
    await vi.advanceTimersByTimeAsync(20_000);
    stop();
    return {
      calls: api.calls("GET /api/me/positions").length,
      result: observer.getCurrentResult(),
    };
  }

  test("is asked twice more when there was no connection", async () => {
    const { calls, result } = await read(noAnswer);

    expect(calls).toBe(3);
    expect(result.error?.kind).toBe("network");
  });

  test("is asked twice more when the API is temporarily unavailable", async () => {
    expect((await read(errorPage(502))).calls).toBe(3);
    expect(
      (await read(apiError(503, "SERVICE_UNAVAILABLE", "This is temporarily unavailable."))).calls,
    ).toBe(3);
  });

  test("is not asked again when the API gave an answer", async () => {
    expect((await read(apiError(404, "NOT_FOUND", "Not found."))).calls).toBe(1);
    expect((await read(apiError(403, "FORBIDDEN", "You are not allowed to do this."))).calls).toBe(
      1,
    );
    expect((await read(apiError(500, "INTERNAL_ERROR", "Something went wrong."))).calls).toBe(1);
    expect((await read(apiError(429, "RATE_LIMITED", "Too many attempts."))).calls).toBe(1);
  });
});

describe("a write that failed", () => {
  test("is never sent again by itself", async () => {
    const api = mockApi({ "POST /api/codes/redeem": noAnswer });
    const client = createQueryClient();

    await new MutationObserver(client, redeemCodeMutation).mutate("4F7K-Q2M9-XAB3").catch(() => {});

    expect(api.calls("POST /api/codes/redeem")).toHaveLength(1);
  });
});

describe("an answer that says the caller is not who the app thinks", () => {
  function watchMe() {
    const api = mockApi({ "GET /api/me": json(200, me()) });
    const client = createTestQueryClient();
    client.setQueryData(keys.me, me());
    // `/me` is on screen, as it always is inside the app.
    new QueryObserver(client, { ...meQuery, refetchOnMount: false }).subscribe(() => {});
    return { api, client };
  }

  test.each(["UNAUTHENTICATED", "PROFILE_REQUIRED", "FORBIDDEN"] as const)(
    "%s from any read makes the app ask /me again",
    async (code) => {
      const { api, client } = watchMe();
      api.on("GET /api/markets", apiError(code === "UNAUTHENTICATED" ? 401 : 403, code, "No."));

      await client.fetchQuery(marketsQuery()).catch(() => {});

      await waitFor(() => expect(api.calls("GET /api/me")).toHaveLength(1));
    },
  );

  test("the same from a write does too", async () => {
    const { api, client } = watchMe();
    api.on("POST /api/codes/redeem", apiError(403, "FORBIDDEN", "This account is suspended."));

    await new MutationObserver(client, redeemCodeMutation).mutate("4F7K").catch(() => {});

    await waitFor(() => expect(api.calls("GET /api/me")).toHaveLength(1));
  });

  test("any other error leaves /me alone", async () => {
    const { api, client } = watchMe();
    api.on("GET /api/markets", apiError(404, "NOT_FOUND", "Not found."));

    await client.fetchQuery(marketsQuery()).catch(() => {});
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(api.calls("GET /api/me")).toHaveLength(0);
  });

  test("when /me itself says so, it is not asked in a circle", async () => {
    const api = mockApi({
      "GET /api/me": apiError(403, "FORBIDDEN", "This account is suspended."),
    });
    const client = createTestQueryClient();

    await client.fetchQuery(meQuery).catch(() => {});
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(api.calls("GET /api/me")).toHaveLength(1);
  });
});

describe("useConfig and useMe", () => {
  const wrapperFor = (client: ReturnType<typeof createTestQueryClient>) =>
    function Wrapper({ children }: { children: ReactNode }) {
      return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
    };

  test("return what start-up loaded, and ask for nothing when a component mounts", () => {
    const api = mockApi();
    const client = createTestQueryClient();
    client.setQueryData(keys.config, config({ voucher_expiry_days: 14 }));
    client.setQueryData(keys.me, me({ username: "polyterrasse" }));

    const { result } = renderHook(() => ({ config: useConfig(), me: useMe() }), {
      wrapper: wrapperFor(client),
    });

    expect(result.current.config.voucher_expiry_days).toBe(14);
    expect(result.current.me.username).toBe("polyterrasse");
    expect(api.requests).toHaveLength(0);
  });

  test("follow the cache: a balance a write put there is the balance everywhere", async () => {
    mockApi();
    const client = createTestQueryClient();
    client.setQueryData(keys.config, config());
    client.setQueryData(keys.me, me({ balance_cr: "240" }));
    function Balance() {
      return <p>balance {useMe().balance_cr}</p>;
    }
    render(<Balance />, { wrapper: wrapperFor(client) });
    expect(screen.getByText("balance 240")).toBeInTheDocument();

    act(() => {
      client.setQueryData(keys.me, me({ balance_cr: "215" }));
    });

    expect(await screen.findByText("balance 215")).toBeInTheDocument();
  });

  test("say so when they are used outside start-up's gate", () => {
    mockApi({ "GET /api/config": json(200, config()), "GET /api/me": json(200, me()) });
    const client = createTestQueryClient();
    vi.spyOn(console, "error").mockImplementation(() => {});

    expect(() => renderHook(() => useConfig(), { wrapper: wrapperFor(client) })).toThrow(
      /outside the app's start-up gate/,
    );
    expect(() => renderHook(() => useMe(), { wrapper: wrapperFor(client) })).toThrow(
      /outside the app's start-up gate/,
    );
  });
});
