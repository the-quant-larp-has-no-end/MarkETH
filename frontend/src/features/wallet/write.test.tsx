import { onlineManager, useQuery } from "@tanstack/react-query";
import { act, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import type { ApiError } from "@/lib/api/errors";
import { issueVoucherMutation } from "@/lib/api/mutations";
import { meQuery, vouchersQuery } from "@/lib/api/queries";
import { me, voucherIssued, voucherItem } from "@/test/fixtures";
import { apiError, deferred, type Handler, json, mockApi, noAnswer } from "@/test/http";
import { createTestQueryClient, renderHookWithProviders } from "@/test/render";

import { useWrite } from "./write";

const KEY = ["wallet", "issue-voucher"] as const;
const ISSUE = "POST /api/vouchers";
const VOUCHERS = "GET /api/me/vouchers";
const ME = "GET /api/me";

/** `POST /vouchers` with the two reads it would have changed, as the Wallet holds them. */
function useIssue(onNoAnswer?: (error: ApiError) => void) {
  const vouchers = useQuery(vouchersQuery);
  const profile = useQuery(meQuery);
  return useWrite(issueVoucherMutation, { key: KEY, reads: [vouchers, profile], onNoAnswer });
}

function setUp(issue: Handler) {
  const api = mockApi({
    [ME]: json(200, me()),
    [VOUCHERS]: json(200, { items: [] }),
    [ISSUE]: issue,
  });
  const onNoAnswer = vi.fn();
  const view = renderHookWithProviders(() => useIssue(onNoAnswer));
  return { api, onNoAnswer, ...view };
}

/** The reads of the hook have answered once: the screen is at rest. */
async function atRest(api: ReturnType<typeof mockApi>) {
  await waitFor(() => expect(api.calls(VOUCHERS)).toHaveLength(1));
  await waitFor(() => expect(api.calls(ME)).toHaveLength(1));
}

afterEach(() => {
  onlineManager.setOnline(true);
});

describe("one request at a time", () => {
  test("two sends in one moment are one request", async () => {
    const answer = deferred();
    const { api, result } = setUp(answer.handler);
    await atRest(api);

    const sent: boolean[] = [];
    act(() => {
      sent.push(result.current.send("coffee"));
      sent.push(result.current.send("coffee"));
      sent.push(result.current.send("tea"));
    });

    // It says which call became a request, so that a screen can follow that one alone.
    expect(sent).toEqual([true, false, false]);
    await waitFor(() => expect(result.current.sending).toBe(true));
    expect(result.current.blocked).toBe(true);
    expect(api.calls(ISSUE)).toHaveLength(1);
    expect(api.lastCall(ISSUE)?.body).toEqual({ product_id: "coffee" });

    answer.resolve(json(201, voucherIssued()));
    await waitFor(() => expect(result.current.sending).toBe(false));
    expect(api.calls(ISSUE)).toHaveLength(1);
  });

  test("a request that was sent before the screen was left still counts when it is opened again", async () => {
    const answer = deferred();
    const client = createTestQueryClient();
    const api = mockApi({
      [ME]: json(200, me()),
      [VOUCHERS]: json(200, { items: [] }),
      [ISSUE]: answer.handler,
    });
    const first = renderHookWithProviders(() => useIssue(), { client });
    act(() => first.result.current.send("coffee"));
    await waitFor(() => expect(api.calls(ISSUE)).toHaveLength(1));
    first.unmount();

    const second = renderHookWithProviders(() => useIssue(), { client });

    expect(second.result.current.blocked).toBe(true);
    act(() => second.result.current.send("coffee"));
    expect(api.calls(ISSUE)).toHaveLength(1);

    answer.resolve(json(201, voucherIssued()));
    await waitFor(() => expect(second.result.current.blocked).toBe(false));
    expect(api.calls(ISSUE)).toHaveLength(1);
  });

  test("a request goes out whatever the browser thinks of the connection, and is never kept for later", async () => {
    const { api, result } = setUp(noAnswer);
    await atRest(api);
    onlineManager.setOnline(false);

    act(() => result.current.send("coffee"));

    // Tried at once and failed at once: nothing waits to go out by itself.
    await waitFor(() => expect(result.current.unanswered?.kind).toBe("network"));
    expect(api.calls(ISSUE)).toHaveLength(1);
    expect(result.current.sending).toBe(false);
  });
});

describe("the answer", () => {
  test("is held while it is wanted, and forgotten here and in the cache when it is not", async () => {
    const { api, client, result } = setUp(json(201, voucherIssued({ code: "7KQ2M9XA" })));
    await atRest(api);
    const onSuccess = vi.fn();

    act(() => result.current.send("coffee", { onSuccess }));

    await waitFor(() => expect(result.current.data?.code).toBe("7KQ2M9XA"));
    expect(onSuccess).toHaveBeenCalledWith(expect.objectContaining({ code: "7KQ2M9XA" }));
    expect(result.current.error).toBeNull();
    expect(result.current.unanswered).toBeNull();
    expect(result.current.blocked).toBe(false);

    act(() => result.current.forget());

    // Gone from the cache at once, and from the hook with its next render.
    expect(client.getMutationCache().findAll({ mutationKey: KEY })).toHaveLength(0);
    await waitFor(() => expect(result.current.data).toBeUndefined());
  });

  test("a refusal is an answer: it is kept as the error, and nothing has to be read again", async () => {
    const { api, onNoAnswer, result } = setUp(
      apiError(422, "VALIDATION_ERROR", "This product is not on the list.", {
        field: "product_id",
      }),
    );
    await atRest(api);
    const onError = vi.fn();

    act(() => result.current.send("gone", { onError }));

    await waitFor(() => expect(result.current.error?.code).toBe("VALIDATION_ERROR"));
    expect(onError).toHaveBeenCalledTimes(1);
    expect(result.current.unanswered).toBeNull();
    expect(result.current.waiting).toBe(false);
    expect(result.current.blocked).toBe(false);
    expect(onNoAnswer).not.toHaveBeenCalled();
    expect(api.calls(VOUCHERS)).toHaveLength(1);

    act(() => result.current.forget());
    await waitFor(() => expect(result.current.error).toBeNull());
  });
});

describe("a request without an answer", () => {
  test("is told at once, and blocks the write until every read has answered since", async () => {
    const { api, onNoAnswer, result } = setUp(noAnswer);
    await atRest(api);
    const vouchers = deferred();
    const profile = deferred();
    api.on(VOUCHERS, vouchers.handler);
    api.on(ME, profile.handler);

    act(() => result.current.send("coffee"));

    // Known before the reads have answered: the screen can say so while it reads again.
    await waitFor(() => expect(result.current.unanswered?.kind).toBe("network"));
    expect(onNoAnswer).toHaveBeenCalledTimes(1);
    expect(result.current.sending).toBe(false);
    expect(result.current.waiting).toBe(true);
    expect(result.current.blocked).toBe(true);
    expect(result.current.rereadError).toBeNull();
    await waitFor(() => expect(api.calls(VOUCHERS)).toHaveLength(2));

    act(() => result.current.send("coffee"));
    expect(api.calls(ISSUE)).toHaveLength(1);

    vouchers.resolve(json(200, { items: [voucherItem()] }));
    await waitFor(() => expect(api.calls(ME)).toHaveLength(2));
    expect(result.current.blocked).toBe(true);

    profile.resolve(json(200, me({ balance_cr: "205" })));
    await waitFor(() => expect(result.current.blocked).toBe(false));
    expect(result.current.waiting).toBe(false);
    // It still says what happened, until the screen is told to stop.
    expect(result.current.unanswered?.kind).toBe("network");

    act(() => result.current.dismiss());
    expect(result.current.unanswered).toBeNull();
  });

  test("stays blocked when the reads fail too, and reads them again when asked", async () => {
    const { api, result } = setUp(() => apiError(500, "INTERNAL_ERROR", "Something went wrong."));
    await atRest(api);
    api.on(VOUCHERS, noAnswer);

    act(() => result.current.send("coffee"));

    await waitFor(() => expect(result.current.rereadError?.kind).toBe("network"));
    expect(result.current.unanswered?.code).toBe("INTERNAL_ERROR");
    expect(result.current.sending).toBe(false);
    expect(result.current.waiting).toBe(true);
    expect(result.current.blocked).toBe(true);

    // It cannot be talked out of waiting.
    act(() => result.current.dismiss());
    expect(result.current.unanswered?.code).toBe("INTERNAL_ERROR");
    act(() => result.current.send("coffee"));
    expect(api.calls(ISSUE)).toHaveLength(1);

    api.on(VOUCHERS, json(200, { items: [] }));
    act(() => result.current.reread());

    await waitFor(() => expect(result.current.blocked).toBe(false));
    expect(result.current.rereadError).toBeNull();
    act(() => result.current.send("coffee"));
    await waitFor(() => expect(api.calls(ISSUE)).toHaveLength(2));
  });
});
