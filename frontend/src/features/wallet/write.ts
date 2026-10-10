"use client";

import {
  type MutationKey,
  useIsMutating,
  useMutation,
  type UseMutationOptions,
  useQueryClient,
} from "@tanstack/react-query";
import { useState } from "react";

import { type ApiError, isApiError } from "@/lib/api/errors";

/** A read the Wallet shows, as far as a write looks at it. */
export type Read = {
  dataUpdatedAt: number;
  error: ApiError | null;
  isFetching: boolean;
  refetch: () => unknown;
};

/** One of the writes of `lib/api/mutations.ts`, with the cache handling it brings. */
type WriteOptions<TData, TVariables> = Omit<
  UseMutationOptions<TData, ApiError, TVariables>,
  "mutationKey"
>;

type Callbacks<TData> = {
  onSuccess?: (data: TData) => void;
  onError?: (error: ApiError) => void;
};

export type WriteSetup = {
  /** Tells this write apart from every other in the cache. */
  key: MutationKey;
  /**
   * What the write would have changed. After a request without an answer every one of
   * them has to answer before the write may be sent again.
   */
  reads?: readonly Read[];
  /** Called at the moment a request turns out to have got no answer. */
  onNoAnswer?: (error: ApiError) => void;
};

export type Write<TData, TVariables> = {
  /** The answer of the last request, until `forget()`. */
  data: TData | undefined;
  /** What the last request failed with, once it is over. Null while one is on its way. */
  error: ApiError | null;
  /** A request is on its way and nothing is known about it yet. */
  sending: boolean;
  /**
   * The request that got no answer (no response, or an error of the server), from the
   * moment it failed. Nobody can say whether it was carried out.
   */
  unanswered: ApiError | null;
  /** After a request without an answer: its `reads` have not answered since. */
  waiting: boolean;
  /** While `waiting`: what a read that was asked again failed with. */
  rereadError: ApiError | null;
  /** Asks the `reads` once more. */
  reread: () => void;
  /** Nothing can be sent now: a request is on its way, or `waiting`. */
  blocked: boolean;
  /** Sends one request and says so. While `blocked` it sends nothing and returns false. */
  send: (variables: TVariables, callbacks?: Callbacks<TData>) => boolean;
  /**
   * Drops the answer or the error of the last request, here and in the cache. A request
   * that is still on its way goes on, unwatched: its answer is shown to nobody, and
   * `sending` stays true until it is there.
   */
  forget: () => void;
  /** Stops saying that a request got no answer, once the reads have answered. */
  dismiss: () => void;
};

/**
 * One write of the Wallet, sent the way DESIGN.md §14 asks for.
 *
 * - **One request at a time.** `send` asks the cache, not the last render, whether a
 *   request with this `key` is on its way: two taps in one moment are one request, and so
 *   are a tap, a look at another screen and a second tap.
 * - **A request without an answer is not sent again blindly** ("Other writes without an
 *   answer"). The write of `lib/api/mutations.ts` reads again what it would have changed.
 *   `unanswered` says so from the moment the request failed, which is earlier than the
 *   write itself settles; `waiting` stays true until every one of `reads` has answered
 *   since, also when reading them again failed. Only then does `send` work again.
 *
 * An answer that carries a voucher code is held by this hook while its view is open:
 * `forget()` drops it from the cache too (DESIGN.md §13, "A voucher code lives on screen
 * only").
 */
export function useWrite<TData, TVariables>(
  options: WriteOptions<TData, TVariables>,
  { key, reads = [], onNoAnswer }: WriteSetup,
): Write<TData, TVariables> {
  const client = useQueryClient();
  const [unanswered, setUnanswered] = useState<ApiError | null>(null);
  const request = options.mutationFn;

  const mutation = useMutation<TData, ApiError, TVariables>({
    ...options,
    mutationKey: key,
    mutationFn: async (variables, context) => {
      if (request === undefined) throw new Error("A write needs its request.");
      try {
        return await request(variables, context);
      } catch (error) {
        // Told here, before the write's own handling reads anything again.
        if (isApiError(error) && error.outcomeUnknown) {
          setUnanswered(error);
          onNoAnswer?.(error);
        }
        throw error;
      }
    },
  });

  /** Takes every answered request of this write out of the cache. */
  const dropSettled = () => {
    const cache = client.getMutationCache();
    const settled = cache.findAll({
      mutationKey: key,
      predicate: (candidate) => candidate.state.status !== "pending",
    });
    for (const answered of settled) cache.remove(answered);
  };

  // Counts a request of an earlier visit to the screen too, and the re-read after it.
  const busy = useIsMutating({ mutationKey: key }) > 0;
  const waiting =
    unanswered !== null && !reads.every((read) => read.dataUpdatedAt >= unanswered.receivedAt);
  const failedRead = waiting
    ? reads.find((read) => read.error !== null && !read.isFetching)
    : undefined;

  return {
    data: mutation.data,
    error: mutation.error,
    sending: busy && unanswered === null,
    unanswered,
    waiting,
    rereadError: failedRead?.error ?? null,
    reread: () => {
      for (const read of reads) void read.refetch();
    },
    blocked: busy || waiting,
    send: (variables, callbacks = {}) => {
      if (waiting || client.isMutating({ mutationKey: key }) > 0) return false;
      setUnanswered(null);
      // An answer nobody was there for any more is not kept beyond the next request.
      dropSettled();
      mutation.mutate(variables, {
        onSuccess: (data) => callbacks.onSuccess?.(data),
        onError: (error) => callbacks.onError?.(error),
      });
      return true;
    },
    forget: () => {
      mutation.reset();
      dropSettled();
    },
    dismiss: () => {
      if (!waiting) setUnanswered(null);
    },
  };
}
