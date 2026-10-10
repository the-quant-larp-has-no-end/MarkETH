import createClient, { type Client } from "openapi-fetch";

import { recoverPortalSession } from "../session-recovery";
import { ApiError, toApiError } from "./errors";
import type { paths } from "./schema";

export type ApiClient = Client<paths>;

let client: ApiClient | undefined;

/**
 * The typed client of `/api`, on the page's own origin. This file is the only one that
 * calls `fetch`.
 *
 * It is made on first use, because this module is also evaluated on the server, where
 * there is no page and no call is ever made. `Accept: application/json` on every request
 * is what makes the portal answer a call it has no session for with a 401 instead of a
 * redirect to its sign-in (DESIGN.md §14).
 */
export function api(): ApiClient {
  client ??= createClient<paths>({
    baseUrl: window.location.origin,
    headers: { Accept: "application/json" },
    // Looked up at call time, so that a test can put its own in place.
    fetch: (request) => globalThis.fetch(request),
  });
  return client;
}

type Answer<T> = { data?: T; error?: unknown; response: Response };

/**
 * Runs one API call and returns its body, or throws an `ApiError`: for an answer of the
 * API, for the portal's 401, for an error page and for no answer at all. Every read and
 * every write of the app goes through here.
 *
 *     const me = await call((api) => api.GET("/api/me"));
 */
export async function call<T>(run: (api: ApiClient) => Promise<Answer<T>>): Promise<T> {
  let answer: Answer<T>;
  try {
    answer = await run(api());
  } catch (cause) {
    // A 2xx whose body is not JSON is somebody else's page; anything else never arrived.
    throw cause instanceof SyntaxError ? ApiError.unavailable(0, cause) : ApiError.network(cause);
  }
  if (answer.response.ok) return answer.data as T;

  const error = toApiError(answer.response.status, answer.error);
  if (error.kind === "portal") error.recovering = recoverPortalSession();
  throw error;
}
