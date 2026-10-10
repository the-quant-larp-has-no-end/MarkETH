"use client";

import type { UseMutationOptions } from "@tanstack/react-query";

import type { ApiError } from "@/lib/api/errors";
import type { MarketDetailResponse } from "@/lib/api/schema";
import { useSubmit } from "@/lib/api/submit";

/** One of the seven admin actions on a market, as `lib/api/mutations.ts` defines it. */
export type MarketActionOptions<Body> = UseMutationOptions<
  MarketDetailResponse,
  ApiError,
  { marketId: string; body: Body }
>;

/**
 * One admin action on one market, for the dialog that asks for it. The answer of every
 * action is the market, and the mutation puts it in the cache, so the screen behind the
 * dialog updates from it; `onDone` is the dialog's own part, which is to close.
 *
 * `send` sends nothing while a request of this dialog is out: a second submit is stopped
 * in the step that would send it, whatever the dialog has drawn by then (DESIGN.md §14).
 * After a refusal the mutation has already asked for the market again where the refusal
 * means that the dialog is behind (`CONFLICT`, `LIMIT_EXCEEDED`, `FORBIDDEN`), and after no
 * answer it stays pending until the market has been read again.
 */
export function useMarketAction<Body>(
  options: MarketActionOptions<Body>,
  marketId: string,
  onDone: () => void,
) {
  const submit = useSubmit(options);

  return {
    error: submit.error,
    pending: submit.isPending,
    send(body: Body) {
      submit.mutate({ marketId, body }, { onSuccess: onDone });
    },
    /** An answer is about what was sent: it holds until the input changes. */
    edited() {
      if (submit.isError) submit.reset();
    },
  };
}
