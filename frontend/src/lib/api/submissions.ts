"use client";

import {
  MutationObserver,
  type MutationObserverOptions,
  type QueryClient,
  useQueryClient,
} from "@tanstack/react-query";
import { useCallback, useSyncExternalStore } from "react";

import { newUuid } from "../uuid";
import { ApiError, GENERIC_MESSAGE, isApiError, keepsSubmission } from "./errors";

/**
 * A write that carries an Idempotency-Key, and how long its request is kept (DESIGN.md
 * §14, "A submitted trade" and "An adjustment of tokens").
 *
 * The same key with the same body returns the same result and never books a second one.
 * So the confirming tap serialises the request body once, makes one key for it and keeps
 * both as an `Attempt`. They stay until an answer says what became of that request: a 2xx
 * (the write exists) or a 4xx other than 429 (it was refused and nothing was booked). Any
 * 5xx, a 429, the portal's 401 and a request without a response say nothing about it: the
 * attempt stays, and "Try again" sends the stored body with the stored key, unchanged. A
 * body built a second time would be another request and could be booked twice.
 *
 * The trade ticket and the adjustment of a user's tokens are the two writes of this kind.
 * Each describes itself in `SubmissionRules` and gets a `SubmissionStore` from here; none
 * of them keeps a body or a key of its own.
 */

/**
 * The JSON of a request, exactly as it goes out. It is a string; its type remembers what
 * it was serialised from, so that a stored body only fits the write it was made for.
 */
export type Serialised<Request> = string & { readonly serialisedFrom?: Request };

/** A request as it is sent: the body serialised once, and the one key that belongs to it. */
export type Submission<Request extends object = object> = {
  /** What the write is about, as the id in its path: the market of a trade, the user of an adjustment. */
  readonly target: string;
  readonly body: Serialised<Request>;
  readonly idempotencyKey: string;
};

/** Serialises a request once and gives it its key. For the confirming tap, never for "Try again". */
export function newSubmission<Request extends object>(
  target: string,
  request: Request,
): Submission<Request> {
  return { target, body: JSON.stringify(request), idempotencyKey: newUuid() };
}

/** A write that went out and has no verdict yet, with what its surface showed when it was sent. */
export type Attempt<Sent extends object> = Sent & {
  /** The body and its key, exactly as they went out. Never built again, never re-keyed. */
  readonly submission: Submission;
  /** True while a request is on its way. */
  readonly sending: boolean;
  /** Why the latest request brought no verdict. Null while the first one is on its way. */
  readonly error: ApiError | null;
};

/** What the surface of one target holds: its own `Rest`, and the write that waits. */
export type SubmissionState<Sent extends object, Rest extends object> = Rest & {
  /** Set from the confirming tap until a 2xx or a 4xx other than 429. While set, nothing else is offered. */
  readonly attempt: Attempt<Sent> | null;
};

/** What a verdict is told about the write it belongs to. */
type Settled<Sent extends object, Rest extends object> = {
  target: string;
  /** What the target held beside the attempt when the verdict arrived. */
  rest: Rest;
  /** The attempt the verdict is about. It is dropped with it. */
  attempt: Attempt<Sent>;
  /** Whether a surface shows the target right now, or the verdict arrives unseen. */
  onScreen: boolean;
  client: QueryClient;
};

/**
 * One kind of write, described once, outside every component:
 *
 *     const adjustments: SubmissionRules<AdjustBalanceRequest, BalanceAdjustmentResponse, Sent, Rest> = {
 *       mutation: adjustUserBalanceMutation,
 *       untouched: { refusal: null },
 *       refused: ({ error }) => ({ refusal: error }),
 *     };
 *
 * `Request` is the body, `Answer` what a 2xx carries, `Sent` what the surface showed when
 * it was sent (so that it can show the same while the write waits), and `Rest` whatever
 * else the surface holds per target: a refusal, a receipt, what is chosen in a form.
 */
export type SubmissionRules<
  Request extends object,
  Answer,
  Sent extends object,
  Rest extends object,
> = {
  /** The write: the one of `lib/api/mutations.ts` that sends a `Submission` of this request. */
  mutation: MutationObserverOptions<Answer, ApiError, Submission<Request>>;
  /** What a target holds beside its attempt before anything happened. */
  untouched: Rest;
  /**
   * A 2xx: the write exists. Returns what the target holds from then on; without this it
   * is `untouched` again.
   */
  answered?: (settled: Settled<Sent, Rest> & { answer: Answer }) => Rest;
  /**
   * A 4xx other than 429: refused, nothing was booked. Returns what the target holds from
   * then on; without this it keeps what it held.
   */
  refused?: (settled: Settled<Sent, Rest> & { error: ApiError }) => Rest;
};

/**
 * What the surface that sent a request does itself once it has its verdict: a toast,
 * closing a dialog. Like the callbacks of `mutate()`, they are called only while a surface
 * still shows the target.
 */
export type SubmissionCallbacks<Answer> = {
  answered?: (answer: Answer) => void;
  refused?: (error: ApiError) => void;
};

export type SubmissionStore<
  Request extends object,
  Answer,
  Sent extends object,
  Rest extends object,
> = {
  /** What one target holds. The same object until something changes. */
  read(target: string): SubmissionState<Sent, Rest>;
  /** Follows one target while a surface shows it. Returns how to stop. */
  watch(target: string, onChange: () => void): () => void;
  /**
   * Changes what the target holds beside its attempt. While a write waits it changes
   * nothing and says so with false: the surface shows the write as it was sent.
   */
  change(target: string, next: (rest: Rest) => Rest): boolean;
  /**
   * The confirming tap: one body, one key, one request. `sent` is what the surface showed.
   * While a write of this target waits it sends nothing and returns false, so a second tap
   * is never a second request, whatever the screen has drawn by then.
   */
  submit(
    target: string,
    request: Request,
    sent: Sent,
    options?: SubmissionCallbacks<Answer> & { rest?: (rest: Rest) => Rest },
  ): boolean;
  /**
   * "Try again": the stored body with the stored key. It sends nothing and returns false
   * while one is on its way, and when nothing waits.
   */
  retry(target: string, callbacks?: SubmissionCallbacks<Answer>): boolean;
};

/**
 * What went wrong, as an `ApiError`. `call()` throws nothing else, so anything else is a
 * fault in handling an answer. Nobody can then say what became of the write, which is the
 * case of a request without a response: the attempt is kept.
 */
function asApiError(failure: unknown): ApiError {
  if (isApiError(failure)) return failure;
  return new ApiError({
    kind: "network",
    status: 0,
    code: null,
    message: GENERIC_MESSAGE,
    cause: failure,
  });
}

function createSubmissionStore<
  Request extends object,
  Answer,
  Sent extends object,
  Rest extends object,
>(
  client: QueryClient,
  rules: SubmissionRules<Request, Answer, Sent, Rest>,
): SubmissionStore<Request, Answer, Sent, Rest> {
  type State = SubmissionState<Sent, Rest>;
  /** An attempt as the store holds it: its body still knows which request it is. */
  type Kept = Attempt<Sent> & { readonly submission: Submission<Request> };
  type Entry = { rest: Rest; attempt: Kept | null; state: State };

  const UNTOUCHED: State = { ...rules.untouched, attempt: null };
  const entries = new Map<string, Entry>();
  const watchers = new Map<string, Set<() => void>>();

  // The one caller of the write, with the cache handling it brings. Like every write of
  // the app it goes out at once or fails at once (`createQueryClient`): nothing of it is
  // held back to be sent later, without a tap.
  const sender = new MutationObserver(client, rules.mutation);

  const entry = (target: string): Entry =>
    entries.get(target) ?? { rest: rules.untouched, attempt: null, state: UNTOUCHED };

  const write = (target: string, rest: Rest, attempt: Kept | null): void => {
    if (rest === rules.untouched && attempt === null) entries.delete(target);
    else entries.set(target, { rest, attempt, state: { ...rest, attempt } });
    for (const onChange of [...(watchers.get(target) ?? [])]) onChange();
  };

  const onScreen = (target: string): boolean => (watchers.get(target)?.size ?? 0) > 0;

  /** The attempt of `target`, if it is still the one this request was sent for. */
  const waitingFor = (target: string, submission: Submission<Request>): Entry | null => {
    const held = entries.get(target);
    return held?.attempt?.submission === submission ? held : null;
  };

  /** A 2xx: the write exists. The body and its key have done their work. */
  const answered = (
    target: string,
    submission: Submission<Request>,
    answer: Answer,
    callbacks: SubmissionCallbacks<Answer>,
  ): void => {
    const held = waitingFor(target, submission);
    if (held === null || held.attempt === null) return;
    const shown = onScreen(target);
    const settled = { target, rest: held.rest, attempt: held.attempt, onScreen: shown, client };
    write(target, rules.answered ? rules.answered({ ...settled, answer }) : rules.untouched, null);
    if (shown) callbacks.answered?.(answer);
  };

  const failed = (
    target: string,
    submission: Submission<Request>,
    error: ApiError,
    callbacks: SubmissionCallbacks<Answer>,
  ): void => {
    const held = waitingFor(target, submission);
    if (held === null || held.attempt === null) return;

    if (keepsSubmission(error)) {
      // No verdict. The same body and the same key wait for "Try again".
      write(target, held.rest, { ...held.attempt, sending: false, error });
      return;
    }

    // A verdict: refused, nothing booked. The next confirming tap builds another body
    // with another key.
    const shown = onScreen(target);
    const settled = { target, rest: held.rest, attempt: held.attempt, onScreen: shown, client };
    write(target, rules.refused ? rules.refused({ ...settled, error }) : held.rest, null);
    if (shown) callbacks.refused?.(error);
  };

  const send = (
    target: string,
    submission: Submission<Request>,
    callbacks: SubmissionCallbacks<Answer>,
  ): void => {
    sender.mutate(submission).then(
      (answer) => answered(target, submission, answer, callbacks),
      (failure: unknown) => failed(target, submission, asApiError(failure), callbacks),
    );
  };

  /** The surface left the screen for good: only a write without a verdict outlives that. */
  const leave = (target: string): void => {
    if (entries.get(target)?.attempt === null) entries.delete(target);
  };

  return {
    read: (target) => entries.get(target)?.state ?? UNTOUCHED,

    watch(target, onChange) {
      let listening = watchers.get(target);
      if (listening === undefined) {
        listening = new Set();
        watchers.set(target, listening);
      }
      const set = listening;
      set.add(onChange);

      return () => {
        set.delete(onChange);
        if (set.size > 0) return;
        // Closed, or mounted afresh in the same commit (a sheet turning into a dialog)?
        // Look once that commit is over: a surface that is back keeps what it showed.
        setTimeout(() => {
          if (set.size === 0) leave(target);
        }, 0);
      };
    },

    change(target, next) {
      const held = entry(target);
      if (held.attempt !== null) return false;
      write(target, next(held.rest), null);
      return true;
    },

    submit(target, request, sent, options = {}) {
      const held = entry(target);
      if (held.attempt !== null) return false;

      const submission = newSubmission(target, request);
      write(target, options.rest ? options.rest(held.rest) : held.rest, {
        ...sent,
        submission,
        sending: true,
        error: null,
      });
      send(target, submission, options);
      return true;
    },

    retry(target, callbacks = {}) {
      const held = entry(target);
      if (held.attempt === null || held.attempt.sending) return false;

      write(target, held.rest, { ...held.attempt, sending: true });
      send(target, held.attempt.submission, callbacks);
      return true;
    },
  };
}

const stores = new WeakMap<QueryClient, Map<object, unknown>>();

/**
 * The store of one kind of write for one query client. The app has one client for as long
 * as the page lives, so this is "in memory": what is kept survives every sheet, dialog
 * and route, belongs to no component, is written to no browser storage, and is gone with
 * a reload. The market, the balance and the lists then show what the server recorded.
 *
 * The rules are what a store is found by: define them once, at the top of a module, and
 * pass that same object every time.
 */
export function submissionStore<
  Request extends object,
  Answer,
  Sent extends object,
  Rest extends object,
>(
  client: QueryClient,
  rules: SubmissionRules<Request, Answer, Sent, Rest>,
): SubmissionStore<Request, Answer, Sent, Rest> {
  let ofClient = stores.get(client);
  if (ofClient === undefined) {
    ofClient = new Map();
    stores.set(client, ofClient);
  }
  let store = ofClient.get(rules) as SubmissionStore<Request, Answer, Sent, Rest> | undefined;
  if (store === undefined) {
    store = createSubmissionStore(client, rules);
    ofClient.set(rules, store);
  }
  return store;
}

/**
 * What one target holds, and the store that changes it, for the surface that shows it.
 * The surface renders what is here and keeps none of it itself, so it shows the same
 * thing after it was closed and opened again.
 *
 *     const { state, store } = useSubmissions(adjustments, user.id);
 *     state.attempt            // the adjustment that waits, as it was sent, or null
 *     store.submit(user.id, { amount_cr, reason }, sent, { answered: close })
 *     store.retry(user.id, { answered: close })
 */
export function useSubmissions<
  Request extends object,
  Answer,
  Sent extends object,
  Rest extends object,
>(
  rules: SubmissionRules<Request, Answer, Sent, Rest>,
  target: string,
): {
  state: SubmissionState<Sent, Rest>;
  store: SubmissionStore<Request, Answer, Sent, Rest>;
} {
  const store = submissionStore(useQueryClient(), rules);
  const subscribe = useCallback(
    (onChange: () => void) => store.watch(target, onChange),
    [store, target],
  );
  const read = useCallback(() => store.read(target), [store, target]);

  return { state: useSyncExternalStore(subscribe, read, read), store };
}
