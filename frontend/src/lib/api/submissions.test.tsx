import { act, screen } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { page } from "@/lib/browser";
import { adminUser, adminUsers, balanceAdjusted, OTHER_USER_ID, USER_ID } from "@/test/fixtures";
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
import { createTestQueryClient, renderWithProviders } from "@/test/render";

import { type ApiError, GENERIC_MESSAGE } from "./errors";
import { keys } from "./keys";
import { adjustUserBalanceMutation } from "./mutations";
import type { AdjustBalanceRequest, BalanceAdjustmentResponse } from "./schema";
import {
  newSubmission,
  type SubmissionRules,
  submissionStore,
  useSubmissions,
} from "./submissions";

/**
 * The store that keeps a body and its Idempotency-Key, with the adjustment of a user's
 * tokens as the write. The trade ticket runs on the same store: what holds here for an
 * adjustment is tested for a trade in `features/trade/ticket-store.test.ts`.
 */

const ADJUST: Route = "POST /api/admin/users/{user_id}/adjustments";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

/** What the dialog showed when the adjustment was sent. */
type Sent = { direction: "ADD" | "REMOVE"; amount: string; reason: string };
/** What the dialog holds beside the adjustment that waits. */
type Rest = { refusal: ApiError | null; notes: number };

const UNTOUCHED: Rest = { refusal: null, notes: 0 };
const seen = { answered: [] as boolean[], refused: [] as boolean[] };

const ADJUSTMENTS: SubmissionRules<AdjustBalanceRequest, BalanceAdjustmentResponse, Sent, Rest> = {
  mutation: adjustUserBalanceMutation,
  untouched: UNTOUCHED,
  answered({ onScreen }) {
    seen.answered.push(onScreen);
    return UNTOUCHED;
  },
  refused({ rest, error, onScreen }) {
    seen.refused.push(onScreen);
    return { ...rest, refusal: error };
  },
};

const request: AdjustBalanceRequest = { amount_cr: "35", reason: "Won the quiz night." };
const sent: Sent = { direction: "ADD", amount: "35", reason: "Won the quiz night." };

function setup(answer: Handler) {
  const api = mockApi({ [ADJUST]: answer });
  const client = createTestQueryClient();
  client.setQueryData(keys.admin.users.list({}), { pages: [adminUsers()], pageParams: [null] });
  const store = submissionStore(client, ADJUSTMENTS);
  /** The Idempotency-Key and the body of every request so far, as they went out. */
  const wire = () =>
    api.calls(ADJUST).map((call) => ({
      key: call.headers.get("Idempotency-Key"),
      body: call.rawBody,
      path: call.path,
    }));
  return { api, client, store, wire };
}

type Store = ReturnType<typeof setup>["store"];

/** Resolves once the request that is on its way has its answer. */
function settled(store: Store, target = OTHER_USER_ID): Promise<void> {
  return vi.waitFor(() => {
    expect(store.read(target).attempt?.sending ?? false).toBe(false);
  });
}

beforeEach(() => {
  seen.answered.length = 0;
  seen.refused.length = 0;
  // The portal's 401 reloads the page; a test stays where it is.
  vi.spyOn(page, "reload").mockImplementation(() => {});
});

describe("newSubmission", () => {
  test("serialises the request once and gives it one key", () => {
    const submission = newSubmission(OTHER_USER_ID, request);

    expect(submission.target).toBe(OTHER_USER_ID);
    expect(submission.body).toBe('{"amount_cr":"35","reason":"Won the quiz night."}');
    expect(submission.idempotencyKey).toMatch(UUID);
    expect(newSubmission(OTHER_USER_ID, request).idempotencyKey).not.toBe(
      submission.idempotencyKey,
    );
  });

  test("keeps an amount as the characters it was typed as", () => {
    // More digits than a float keeps: any detour through a number would change them.
    const amount_cr = "-9007199254740993.000001";

    expect(newSubmission(OTHER_USER_ID, { amount_cr, reason: "x" }).body).toContain(
      `"amount_cr":"${amount_cr}"`,
    );
  });
});

describe("the confirming tap", () => {
  test("sends one body with one key, to the target, and keeps both with what was shown", async () => {
    const { store, wire } = setup(deferred().handler);

    expect(store.submit(OTHER_USER_ID, request, sent)).toBe(true);

    const { attempt } = store.read(OTHER_USER_ID);
    expect(attempt).toMatchObject({ ...sent, sending: true, error: null });
    expect(attempt?.submission.body).toBe('{"amount_cr":"35","reason":"Won the quiz night."}');
    await vi.waitFor(() => expect(wire()).toHaveLength(1));
    expect(wire()[0]).toEqual({
      key: attempt?.submission.idempotencyKey,
      body: attempt?.submission.body,
      path: `/api/admin/users/${OTHER_USER_ID}/adjustments`,
    });
  });

  test("a second tap sends nothing, whatever the screen has drawn by then", async () => {
    const { store, wire } = setup(deferred().handler);

    // Both in one breath: no render lies between them.
    expect(store.submit(OTHER_USER_ID, request, sent)).toBe(true);
    expect(store.submit(OTHER_USER_ID, request, sent)).toBe(false);
    expect(store.submit(OTHER_USER_ID, { amount_cr: "70", reason: "Twice." }, sent)).toBe(false);

    await vi.waitFor(() => expect(wire()).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(wire()).toHaveLength(1);
  });

  test("can change what the target holds in the same step", () => {
    const { store } = setup(deferred().handler);
    store.change(OTHER_USER_ID, (rest) => ({ ...rest, notes: 2 }));

    store.submit(OTHER_USER_ID, request, sent, { rest: (rest) => ({ ...rest, notes: 3 }) });

    expect(store.read(OTHER_USER_ID).notes).toBe(3);
    expect(store.read(OTHER_USER_ID).attempt?.sending).toBe(true);
  });

  test("belongs to its target: another user's adjustment is free", async () => {
    const { api, store, wire } = setup(noAnswer);
    store.submit(OTHER_USER_ID, request, sent);
    await settled(store);

    api.on(ADJUST, deferred().handler);
    expect(store.submit(USER_ID, request, sent)).toBe(true);

    await vi.waitFor(() => expect(wire()).toHaveLength(2));
    expect(wire()[1]?.path).toBe(`/api/admin/users/${USER_ID}/adjustments`);
    expect(wire()[1]?.key).not.toBe(wire()[0]?.key);
  });
});

/** DESIGN.md §14: these outcomes say nothing about the write, so body and key are kept. */
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
    "a 429",
    apiError(429, "RATE_LIMITED", "Too many attempts. Try again later.", {
      retry_after_seconds: 30,
    }),
    "Too many attempts. Try again later.",
  ],
  ["the portal's 401", portal401(), "Your ETH sign-in has ended."],
  ["a request without a response", noAnswer, "No connection. Try again."],
];

describe.each(NO_VERDICT)("after %s", (_name, answer, message) => {
  test("the body and its key are kept, and Try again sends exactly them", async () => {
    const { api, store, wire } = setup(answer);
    const stop = store.watch(OTHER_USER_ID, () => {});

    store.submit(OTHER_USER_ID, request, sent);
    const sentAs = store.read(OTHER_USER_ID).attempt?.submission;
    await settled(store);

    const waiting = store.read(OTHER_USER_ID).attempt;
    expect(waiting?.submission).toBe(sentAs);
    expect(waiting?.error?.message).toBe(message);
    expect(waiting).toMatchObject(sent);
    expect(store.read(OTHER_USER_ID).refusal).toBeNull();
    // Left alone, a write without a verdict stays as it is: nothing goes out by itself.
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(wire()).toHaveLength(1);

    // Nothing else can be sent or changed for this target meanwhile.
    expect(store.submit(OTHER_USER_ID, { amount_cr: "70", reason: "Another." }, sent)).toBe(false);
    expect(store.change(OTHER_USER_ID, (rest) => ({ ...rest, notes: 9 }))).toBe(false);
    expect(store.read(OTHER_USER_ID).notes).toBe(0);

    api.on(ADJUST, json(201, balanceAdjusted()));
    expect(store.retry(OTHER_USER_ID)).toBe(true);
    expect(store.read(OTHER_USER_ID).attempt?.sending).toBe(true);
    await settled(store);

    const [first, replay] = wire();
    expect(wire()).toHaveLength(2);
    expect(replay?.key).toMatch(UUID);
    expect(replay).toEqual(first);
    // The 2xx is the verdict: the adjustment exists, and both are dropped.
    expect(store.read(OTHER_USER_ID).attempt).toBeNull();
    stop();
  });
});

describe("a write that stays unanswered", () => {
  test("two taps on Try again are one request, and Try again does nothing when nothing waits", async () => {
    const { api, store, wire } = setup(noAnswer);
    expect(store.retry(OTHER_USER_ID)).toBe(false);

    store.submit(OTHER_USER_ID, request, sent);
    await settled(store);
    api.on(ADJUST, deferred().handler);

    expect(store.retry(OTHER_USER_ID)).toBe(true);
    expect(store.retry(OTHER_USER_ID)).toBe(false);

    await vi.waitFor(() => expect(wire()).toHaveLength(2));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(wire()).toHaveLength(2);
  });

  test("a fault in handling the answer counts as no answer: the write may exist", async () => {
    // A 201 whose body cannot be read as an adjustment: the request went through.
    const { api, store, wire } = setup(json(201, null));

    store.submit(OTHER_USER_ID, request, sent);
    await settled(store);

    const { attempt } = store.read(OTHER_USER_ID);
    expect(attempt?.error?.message).toBe(GENERIC_MESSAGE);
    expect(attempt?.error?.outcomeUnknown).toBe(true);

    api.on(ADJUST, json(201, balanceAdjusted()));
    store.retry(OTHER_USER_ID);
    await settled(store);
    expect(wire()[1]).toEqual(wire()[0]);
  });

  test("outlives its surface: closed and opened again, it is the same", async () => {
    const { store } = setup(noAnswer);
    const stop = store.watch(OTHER_USER_ID, () => {});
    store.change(OTHER_USER_ID, (rest) => ({ ...rest, notes: 4 }));
    store.submit(OTHER_USER_ID, request, sent);
    await settled(store);
    const waiting = store.read(OTHER_USER_ID);

    stop();
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(store.read(OTHER_USER_ID)).toBe(waiting);
    expect(store.read(OTHER_USER_ID).attempt?.submission).toBe(waiting.attempt?.submission);
  });
});

/** DESIGN.md §14: a 4xx other than 429 is a verdict. The write was refused, nothing was booked. */
const REFUSALS: [name: string, answer: Response][] = [
  ["409 INSUFFICIENT_BALANCE", apiError(409, "INSUFFICIENT_BALANCE", "Fewer tokens than that.")],
  ["422 VALIDATION_ERROR", apiError(422, "VALIDATION_ERROR", "The request is not valid.")],
  ["422 IDEMPOTENCY_KEY_REUSED", apiError(422, "IDEMPOTENCY_KEY_REUSED", "Key already used.")],
  ["403 FORBIDDEN", apiError(403, "FORBIDDEN", "You cannot do this to your own account.")],
  ["404 NOT_FOUND", apiError(404, "NOT_FOUND", "Not found.")],
];

describe.each(REFUSALS)("after a %s", (_name, answer) => {
  test("the body and its key are dropped, and the next confirming tap has another key", async () => {
    const { store, wire } = setup(answer);
    const stop = store.watch(OTHER_USER_ID, () => {});

    store.submit(OTHER_USER_ID, request, sent);
    await settled(store);

    const state = store.read(OTHER_USER_ID);
    expect(state.attempt).toBeNull();
    expect(state.refusal?.status).toBe(answer.status);

    // Nothing waits, so Try again has nothing to send.
    expect(store.retry(OTHER_USER_ID)).toBe(false);
    expect(store.submit(OTHER_USER_ID, request, sent)).toBe(true);
    await settled(store);

    const [first, second] = wire();
    expect(wire()).toHaveLength(2);
    expect(second?.key).toMatch(UUID);
    expect(second?.key).not.toBe(first?.key);
    stop();
  });
});

describe("a verdict", () => {
  test("a 2xx tells the rules whether a surface saw it, and the surface that sent it", async () => {
    const { store } = setup(json(201, balanceAdjusted({ balance_cr: "1015" })));
    const stop = store.watch(OTHER_USER_ID, () => {});
    const answered = vi.fn();
    const refused = vi.fn();

    store.submit(OTHER_USER_ID, request, sent, { answered, refused });
    await settled(store);

    expect(seen.answered).toEqual([true]);
    expect(answered).toHaveBeenCalledTimes(1);
    expect(answered).toHaveBeenCalledWith(balanceAdjusted({ balance_cr: "1015" }));
    expect(refused).not.toHaveBeenCalled();
    // By then the store shows the verdict: nothing waits.
    expect(store.read(OTHER_USER_ID).attempt).toBeNull();
    stop();
  });

  test("a refusal goes to the rules and to the surface, with the API's error", async () => {
    const { store } = setup(
      apiError(409, "INSUFFICIENT_BALANCE", "This account holds fewer tokens than that."),
    );
    const stop = store.watch(OTHER_USER_ID, () => {});
    const refused = vi.fn();

    store.submit(OTHER_USER_ID, request, sent, { refused });
    await settled(store);

    expect(seen.refused).toEqual([true]);
    expect(refused).toHaveBeenCalledTimes(1);
    expect(refused.mock.calls[0]?.[0]).toMatchObject({
      code: "INSUFFICIENT_BALANCE",
      message: "This account holds fewer tokens than that.",
    });
    expect(store.read(OTHER_USER_ID).refusal?.code).toBe("INSUFFICIENT_BALANCE");
    stop();
  });

  test("that arrives when no surface shows the target is not told to the surface that sent it", async () => {
    const answer = deferred();
    const { store } = setup(answer.handler);
    const stop = store.watch(OTHER_USER_ID, () => {});
    const answered = vi.fn();

    store.submit(OTHER_USER_ID, request, sent, { answered });
    stop();
    answer.resolve(json(201, balanceAdjusted()));
    await settled(store);

    expect(seen.answered).toEqual([false]);
    expect(answered).not.toHaveBeenCalled();
    expect(store.read(OTHER_USER_ID).attempt).toBeNull();
  });

  test("of Try again is told to the surface that tapped it", async () => {
    const { api, store } = setup(noAnswer);
    const stop = store.watch(OTHER_USER_ID, () => {});
    const first = vi.fn();
    const second = vi.fn();

    store.submit(OTHER_USER_ID, request, sent, { answered: first });
    await settled(store);
    api.on(ADJUST, json(201, balanceAdjusted()));
    store.retry(OTHER_USER_ID, { answered: second });
    await settled(store);

    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
    stop();
  });

  test("is handled by the write itself too: the user's row holds the answer's balance", async () => {
    const { client, store } = setup(json(201, balanceAdjusted({ balance_cr: "1015" })));

    store.submit(OTHER_USER_ID, request, sent);
    await settled(store);

    const list = client.getQueryData<{ pages: { items: { id: string; balance_cr: string }[] }[] }>(
      keys.admin.users.list({}),
    );
    expect(list?.pages[0]?.items[0]).toEqual({ ...adminUser(), balance_cr: "1015" });
  });
});

describe("what a target holds beside its attempt", () => {
  test("starts untouched, the same object for every target and every read", () => {
    const { store } = setup(deferred().handler);

    expect(store.read(OTHER_USER_ID)).toEqual({ refusal: null, notes: 0, attempt: null });
    expect(store.read(OTHER_USER_ID)).toBe(store.read(OTHER_USER_ID));
    expect(store.read(USER_ID)).toBe(store.read(OTHER_USER_ID));
  });

  test("changes tell the watchers of that target, and of no other", () => {
    const { store } = setup(deferred().handler);
    const mine = vi.fn();
    const other = vi.fn();
    store.watch(OTHER_USER_ID, mine);
    store.watch(USER_ID, other);

    expect(store.change(OTHER_USER_ID, (rest) => ({ ...rest, notes: 1 }))).toBe(true);

    expect(mine).toHaveBeenCalledTimes(1);
    expect(other).not.toHaveBeenCalled();
    expect(store.read(OTHER_USER_ID).notes).toBe(1);
    expect(store.read(USER_ID).notes).toBe(0);
  });

  test("is forgotten once its surface has left the screen", async () => {
    const { store } = setup(deferred().handler);
    const stop = store.watch(OTHER_USER_ID, () => {});
    store.change(OTHER_USER_ID, (rest) => ({ ...rest, notes: 5 }));

    stop();
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(store.read(OTHER_USER_ID).notes).toBe(0);
  });

  test("is kept when the surface is back within the same breath: a sheet that became a dialog", async () => {
    const { store } = setup(deferred().handler);
    const stop = store.watch(OTHER_USER_ID, () => {});
    store.change(OTHER_USER_ID, (rest) => ({ ...rest, notes: 5 }));

    stop();
    store.watch(OTHER_USER_ID, () => {});
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(store.read(OTHER_USER_ID).notes).toBe(5);
  });

  test("lives with its query client and its rules: another client starts empty", async () => {
    const { client, store } = setup(noAnswer);
    store.submit(OTHER_USER_ID, request, sent);
    await settled(store);

    const elsewhere = createTestQueryClient();

    expect(submissionStore(client, ADJUSTMENTS)).toBe(store);
    expect(submissionStore(elsewhere, ADJUSTMENTS)).not.toBe(store);
    expect(submissionStore(elsewhere, ADJUSTMENTS).read(OTHER_USER_ID).attempt).toBeNull();
    // Other rules are another kind of write: they keep a store of their own.
    expect(submissionStore(client, { ...ADJUSTMENTS })).not.toBe(store);
  });
});

describe("useSubmissions", () => {
  function Dialog({ onDone }: { onDone: () => void }) {
    const { state, store } = useSubmissions(ADJUSTMENTS, OTHER_USER_ID);
    const waiting = state.attempt;

    return (
      <div>
        {waiting === null ? (
          <button
            type="button"
            onClick={() => store.submit(OTHER_USER_ID, request, sent, { answered: onDone })}
          >
            Add tokens
          </button>
        ) : (
          <>
            <p>
              {waiting.direction} {waiting.amount}: {waiting.reason}
            </p>
            {waiting.error !== null && <p role="alert">{waiting.error.message}</p>}
            <button
              type="button"
              disabled={waiting.sending}
              onClick={() => store.retry(OTHER_USER_ID, { answered: onDone })}
            >
              Try again
            </button>
          </>
        )}
        {state.refusal !== null && <p role="alert">{state.refusal.message}</p>}
      </div>
    );
  }

  test("shows the write that waits as it was sent, also after the surface was closed and opened again", async () => {
    const api = mockApi({ [ADJUST]: noAnswer });
    const client = createTestQueryClient();
    const onDone = vi.fn();
    const first = renderWithProviders(<Dialog onDone={onDone} />, { client });

    await first.user.click(screen.getByRole("button", { name: "Add tokens" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("No connection. Try again.");
    expect(screen.getByText("ADD 35: Won the quiz night.")).toBeInTheDocument();
    first.unmount();
    await act(() => new Promise((resolve) => setTimeout(resolve, 20)));

    // Opened again for the same user: the same adjustment, and nothing but "Try again".
    const again = renderWithProviders(<Dialog onDone={onDone} />, { client });
    expect(screen.getByText("ADD 35: Won the quiz night.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add tokens" })).not.toBeInTheDocument();

    api.on(ADJUST, json(201, balanceAdjusted()));
    await again.user.click(screen.getByRole("button", { name: "Try again" }));

    await vi.waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole("button", { name: "Add tokens" })).toBeInTheDocument();
    const [sentFirst, sentAgain] = api.calls(ADJUST);
    expect(sentAgain?.rawBody).toBe(sentFirst?.rawBody);
    expect(sentAgain?.headers.get("Idempotency-Key")).toBe(
      sentFirst?.headers.get("Idempotency-Key"),
    );
  });

  test("shows a refusal with the API's message and offers the form again", async () => {
    mockApi({
      [ADJUST]: apiError(409, "INSUFFICIENT_BALANCE", "This account holds fewer tokens than that."),
    });
    const { user } = renderWithProviders(<Dialog onDone={() => {}} />);

    await user.click(screen.getByRole("button", { name: "Add tokens" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This account holds fewer tokens than that.",
    );
    expect(screen.getByRole("button", { name: "Add tokens" })).toBeEnabled();
  });
});
