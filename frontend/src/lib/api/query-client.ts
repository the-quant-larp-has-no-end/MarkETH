import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";

import type { ApiError } from "./errors";
import { keys } from "./keys";

declare module "@tanstack/react-query" {
  /** Every query and mutation goes through `call()`, which throws nothing but `ApiError`. */
  interface Register {
    defaultError: ApiError;
  }
}

/** A failed read is asked this many more times before the screen shows the failure. */
const QUERY_RETRIES = 2;

/**
 * The query client of the app, with its four rules:
 *
 * - A read is tried again only when trying again can help: no connection, or the API
 *   says it is temporarily unavailable. Everything else is an answer and is shown.
 * - A write is never sent again by itself (DESIGN.md §14).
 * - Every request goes out when it is made, whatever the browser thinks of the
 *   connection. A read that cannot get through fails and the screen says "No connection.
 *   Try again."; it is asked again when the connection is back. A write that cannot get
 *   through fails at once: it is never held back to be sent later, when nobody is looking.
 * - An answer that says the caller is not who the app thinks (`UNAUTHENTICATED`,
 *   `PROFILE_REQUIRED`, `FORBIDDEN`) makes the app ask `/me` again, wherever it came
 *   from. Start-up reads that answer and shows the profile set-up or the full-screen
 *   message when `/me` says the same.
 */
export function createQueryClient(): QueryClient {
  const askWhoIsCalling = (error: ApiError, cameFromMe: boolean) => {
    if (cameFromMe) return;
    if (
      error.code === "UNAUTHENTICATED" ||
      error.code === "PROFILE_REQUIRED" ||
      error.code === "FORBIDDEN"
    ) {
      void client.invalidateQueries({ queryKey: keys.me, exact: true });
    }
  };

  const client: QueryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: (failureCount, error) => error.retryable && failureCount < QUERY_RETRIES,
        retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 4000),
        networkMode: "always",
        refetchOnReconnect: true,
      },
      mutations: { retry: false, networkMode: "always" },
    },
    queryCache: new QueryCache({
      onError: (error, query) =>
        askWhoIsCalling(error, query.queryKey.length === 1 && query.queryKey[0] === keys.me[0]),
    }),
    mutationCache: new MutationCache({
      onError: (error) => askWhoIsCalling(error, false),
    }),
  });
  return client;
}
