import type { ErrorCode, QuoteResponse } from "./schema";

/**
 * Where a failed call failed (DESIGN.md §14).
 *
 * - `api`: the API answered `{code, message, details}`. Its `message` is shown as sent.
 * - `portal`: a 401 without a `code`. The portal in front of the app has no session
 *   for the visitor; the page reloads once so that it can sign them in.
 * - `unavailable`: any other answer without a `code` (an error page of the proxy, a
 *   body that is not JSON). Treated as `SERVICE_UNAVAILABLE`.
 * - `network`: no response at all.
 */
export type ApiErrorKind = "api" | "portal" | "network" | "unavailable";

/* The four sentences that are ours, because in these cases the API sent none. */
export const NETWORK_MESSAGE = "No connection. Try again.";
export const UNAVAILABLE_MESSAGE = "This is temporarily unavailable. Try again shortly.";
export const PORTAL_MESSAGE = "Your ETH sign-in has ended.";
/** In place of an API message that makes no sense to a user (`IDEMPOTENCY_KEY_REUSED`),
 *  and for a failure that is not an API answer at all. */
export const GENERIC_MESSAGE = "Something went wrong. Try again.";

export type ErrorDetails = Readonly<Record<string, unknown>>;

type ApiErrorInit = {
  kind: ApiErrorKind;
  status: number;
  code: ErrorCode | null;
  message: string;
  details?: ErrorDetails;
  cause?: unknown;
};

/** Every failure of an API call, whatever went wrong. Thrown by `call()` and nothing else. */
export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  /** The HTTP status, or 0 when no response arrived. */
  readonly status: number;
  /** The API's code. `SERVICE_UNAVAILABLE` for kind `unavailable`; null for `portal` and `network`. */
  readonly code: ErrorCode | null;
  /** The API's `details`, an empty object when it sent none. Read it with the helpers below. */
  readonly details: ErrorDetails;
  /** When the failure arrived, in epoch milliseconds: the start of a retry countdown. */
  readonly receivedAt: number;
  /** True when this failure made the page reload to recover the portal's session. */
  recovering = false;

  constructor(init: ApiErrorInit) {
    super(init.message, init.cause === undefined ? undefined : { cause: init.cause });
    this.name = "ApiError";
    this.kind = init.kind;
    this.status = init.status;
    this.code = init.code;
    this.details = init.details ?? {};
    this.receivedAt = Date.now();
  }

  static network(cause?: unknown): ApiError {
    return new ApiError({
      kind: "network",
      status: 0,
      code: null,
      message: NETWORK_MESSAGE,
      cause,
    });
  }

  static unavailable(status: number, cause?: unknown): ApiError {
    return new ApiError({
      kind: "unavailable",
      status,
      code: "SERVICE_UNAVAILABLE",
      message: UNAVAILABLE_MESSAGE,
      cause,
    });
  }

  static portal(): ApiError {
    return new ApiError({ kind: "portal", status: 401, code: null, message: PORTAL_MESSAGE });
  }

  /**
   * True when nobody can say whether the request was carried out: no response, or an
   * error of the server (DESIGN.md §14, "Other writes without an answer"). A screen then
   * refetches what the write would have changed before the user may act again. A 4xx is
   * an answer: the request was refused.
   */
  get outcomeUnknown(): boolean {
    return this.status < 400 || this.status >= 500;
  }

  /** A read that failed like this is worth asking again by itself. */
  get retryable(): boolean {
    return this.kind === "network" || this.code === "SERVICE_UNAVAILABLE";
  }
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError;
}

/**
 * The sentence to show for an error: the API's `message`, as sent. The one exception is
 * `IDEMPOTENCY_KEY_REUSED`, which should not occur and whose message is about a header;
 * DESIGN.md §14 puts "Something went wrong. Try again." in its place.
 */
export function messageOf(error: ApiError): string {
  return error.code === "IDEMPOTENCY_KEY_REUSED" ? GENERIC_MESSAGE : error.message;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Turns a non-2xx answer into an `ApiError`. The two answers that are not the API's are
 * told apart by their body: the portal's 401 has no `code`, and neither has an error
 * page of the proxy.
 */
export function toApiError(status: number, body: unknown): ApiError {
  if (isRecord(body) && typeof body.code === "string" && typeof body.message === "string") {
    return new ApiError({
      kind: "api",
      status,
      code: body.code as ErrorCode,
      message: body.message,
      details: isRecord(body.details) ? body.details : {},
    });
  }
  if (status === 401) return ApiError.portal();
  return ApiError.unavailable(status);
}

/**
 * DESIGN.md §14, "A submitted trade" and "An adjustment of tokens": whether the request
 * body and its Idempotency-Key are kept after this failure. Only a 4xx other than 429
 * says that the write was refused and nothing was booked. A 5xx, a 429, the portal's 401
 * and a request without a response say nothing about it, so the same body goes out again
 * with the same key.
 */
export function keepsSubmission(error: ApiError): boolean {
  if (error.kind === "network" || error.kind === "portal") return true;
  const refused = error.status >= 400 && error.status < 500 && error.status !== 429;
  return !refused;
}

/** The fresh quote of a `QUOTE_STALE` answer. */
export function staleQuote(error: ApiError): QuoteResponse | null {
  if (error.code !== "QUOTE_STALE" || !isRecord(error.details.quote)) return null;
  return error.details.quote as unknown as QuoteResponse;
}

/** How long a `RATE_LIMITED` answer asks the caller to wait. */
export function retryAfterSeconds(error: ApiError): number | null {
  const seconds = error.details.retry_after_seconds;
  if (error.code !== "RATE_LIMITED" || typeof seconds !== "number") return null;
  return seconds;
}

/** What a `LIMIT_EXCEEDED` on the stake limit says is left, as the strings it sent. */
export function stakeLeft(
  error: ApiError,
): { stake_left_cr: string; stake_limit_cr: string } | null {
  const { stake_left_cr, stake_limit_cr } = error.details;
  if (error.code !== "LIMIT_EXCEEDED") return null;
  if (typeof stake_left_cr !== "string" || typeof stake_limit_cr !== "string") return null;
  return { stake_left_cr, stake_limit_cr };
}

/** Which limit a `LIMIT_EXCEEDED` ran into (`details.limit`). */
export function limitName(error: ApiError): string | null {
  const { limit } = error.details;
  return error.code === "LIMIT_EXCEEDED" && typeof limit === "string" ? limit : null;
}

/** Who the portal says is calling, from a `PROFILE_REQUIRED` answer. `name` may be empty. */
export function portalIdentity(error: ApiError): { name: string; email: string } | null {
  if (error.code !== "PROFILE_REQUIRED") return null;
  const { name, email } = error.details;
  return {
    name: typeof name === "string" ? name : "",
    email: typeof email === "string" ? email : "",
  };
}

/**
 * The messages an error has for single fields, by field name (DESIGN.md §14).
 *
 * A rule a service checks names its field in `details.field` and speaks through the
 * error's `message`. A request that does not match its schema lists `details.errors`,
 * one `{loc, msg}` per field: the field is the last element of `loc`.
 */
export function fieldErrors(error: ApiError): Record<string, string> {
  const found: Record<string, string> = {};
  const { field, errors } = error.details;
  if (typeof field === "string" && field !== "") found[field] = error.message;
  if (Array.isArray(errors)) {
    for (const entry of errors) {
      if (!isRecord(entry) || !Array.isArray(entry.loc) || entry.loc.length === 0) continue;
      const name = String(entry.loc[entry.loc.length - 1]);
      if (name in found) continue;
      found[name] = typeof entry.msg === "string" && entry.msg !== "" ? entry.msg : error.message;
    }
  }
  return found;
}

/**
 * Sorts an error onto a form: the message for each of the form's `fields` the error
 * names, and in `form` the message to show above the submit button when it names none
 * of them. One of the two is always set.
 */
export function placeError<Field extends string>(
  error: ApiError,
  fields: readonly Field[],
): { fields: Partial<Record<Field, string>>; form: string | null } {
  const named = fieldErrors(error);
  const placed: Partial<Record<Field, string>> = {};
  let any = false;
  for (const field of fields) {
    const message = named[field];
    if (message !== undefined) {
      placed[field] = message;
      any = true;
    }
  }
  return { fields: placed, form: any ? null : error.message };
}
