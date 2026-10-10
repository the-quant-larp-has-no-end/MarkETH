"use client";

import { useQuery } from "@tanstack/react-query";

import type { ApiError } from "@/lib/api/errors";
import type { QuoteRequest } from "@/lib/api/keys";
import { quoteQuery } from "@/lib/api/queries";
import type { QuoteResponse } from "@/lib/api/schema";
import { useConfig } from "@/lib/api/session";
import { useDebouncedValue } from "@/lib/hooks";

/** Keys the query while there is nothing to quote. It is never asked for. */
const NOTHING_TO_QUOTE: QuoteRequest = { side: "BUY", outcome: "YES", amount_cr: "" };

export type QuoteRead = {
  /** The quote for exactly the request in hand, or null: nothing to quote, on its way, failed. */
  quote: QuoteResponse | null;
  /** True from the change of the input until its quote, or its failure, is there. */
  loading: boolean;
  /** Why there is no quote: the server's verdict on the input, or a read that failed. */
  error: ApiError | null;
  /** Asks for the same quote again, after a failure. */
  retry: () => void;
};

/**
 * The quote of the trade sheet (DESIGN.md §13): `GET /markets/{market_id}/quote`, asked
 * for `quote_debounce_ms` (from `/config`) after the last change of the input, with the
 * side, the outcome and either the text of the amount field or the preset's shares, as
 * they stand.
 *
 * The quote is about the caller: it applies the stake limit, the balance and the smallest
 * trade, and answers the error the trade itself would get. That is how a typed amount is
 * judged, by the server; `error` carries its verdict.
 *
 * Only an answer for the input as it is now counts, so Confirm stays disabled from a
 * change until the quote for it has arrived. Nothing asks again behind the user's back:
 * not the focus, not a stream event. The user confirms what they were shown, and the
 * server answers `QUOTE_STALE` if the price has moved since (DESIGN.md §14).
 */
export function useQuote(marketId: string, request: QuoteRequest | null): QuoteRead {
  const { quote_debounce_ms } = useConfig();
  // As text, equal requests are equal values, whichever object they arrive in.
  const wanted = request === null ? null : JSON.stringify(request);
  const settled = useDebouncedValue(wanted, quote_debounce_ms);
  const asked = settled === null ? null : (JSON.parse(settled) as QuoteRequest);

  const query = useQuery({
    ...quoteQuery(marketId, asked ?? NOTHING_TO_QUOTE),
    enabled: asked !== null,
  });

  const current = wanted !== null && wanted === settled;
  const quote = current ? (query.data ?? null) : null;
  const error = current && !query.isFetching ? query.error : null;

  return {
    quote,
    error,
    loading: wanted !== null && quote === null && error === null,
    retry: () => void query.refetch(),
  };
}
