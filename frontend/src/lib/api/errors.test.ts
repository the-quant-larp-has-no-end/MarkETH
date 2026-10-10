import { describe, expect, test } from "vitest";

import { quote } from "@/test/fixtures";

import {
  ApiError,
  fieldErrors,
  keepsSubmission,
  limitName,
  placeError,
  portalIdentity,
  retryAfterSeconds,
  stakeLeft,
  staleQuote,
  toApiError,
} from "./errors";

const api = (status: number, code: string, message: string, details: unknown = {}) =>
  toApiError(status, { code, message, details });

describe("toApiError", () => {
  test("an answer with a code is the API's, whatever its status", () => {
    const error = api(409, "USERNAME_TAKEN", "This username is taken.", { field: "username" });

    expect(error.kind).toBe("api");
    expect(error.code).toBe("USERNAME_TAKEN");
    expect(error.message).toBe("This username is taken.");
    expect(error.details).toEqual({ field: "username" });
  });

  test("details that are no object become an empty one", () => {
    expect(api(500, "INTERNAL_ERROR", "Something went wrong.", null).details).toEqual({});
    expect(api(500, "INTERNAL_ERROR", "Something went wrong.", ["x"]).details).toEqual({});
  });

  test("a 401 without a code is the portal's, with or without a body", () => {
    expect(toApiError(401, {}).kind).toBe("portal");
    expect(toApiError(401, undefined).kind).toBe("portal");
    expect(toApiError(401, "").kind).toBe("portal");
  });

  test("anything else without a code is treated as SERVICE_UNAVAILABLE", () => {
    for (const body of [undefined, "<html></html>", { detail: "Not Found" }, { code: 7 }]) {
      const error = toApiError(502, body);
      expect(error.kind).toBe("unavailable");
      expect(error.code).toBe("SERVICE_UNAVAILABLE");
    }
  });
});

describe("what an error says about the request", () => {
  test("a write may have happened after no response or an error of the server, not after a refusal", () => {
    expect(ApiError.network().outcomeUnknown).toBe(true);
    expect(api(500, "INTERNAL_ERROR", "Something went wrong.").outcomeUnknown).toBe(true);
    expect(api(503, "SERVICE_UNAVAILABLE", "Unavailable.").outcomeUnknown).toBe(true);
    expect(toApiError(504, "<html>").outcomeUnknown).toBe(true);
    expect(api(422, "CODE_INVALID", "This code is not valid.").outcomeUnknown).toBe(false);
    expect(api(429, "RATE_LIMITED", "Too many attempts.").outcomeUnknown).toBe(false);
  });

  test("a read is worth asking again only when that can help", () => {
    expect(ApiError.network().retryable).toBe(true);
    expect(toApiError(502, "<html>").retryable).toBe(true);
    expect(api(503, "SERVICE_UNAVAILABLE", "Unavailable.").retryable).toBe(true);
    expect(api(404, "NOT_FOUND", "Not found.").retryable).toBe(false);
    expect(api(403, "FORBIDDEN", "You are not allowed to do this.").retryable).toBe(false);
    expect(ApiError.portal().retryable).toBe(false);
  });

  test("a body and its Idempotency-Key are dropped only after a 2xx or a 4xx other than 429", () => {
    // Refused, nothing was booked: the next Confirm builds another body with another key.
    expect(keepsSubmission(api(409, "QUOTE_STALE", "The price moved."))).toBe(false);
    expect(keepsSubmission(api(422, "INSUFFICIENT_BALANCE", "Not enough tokens."))).toBe(false);
    expect(keepsSubmission(api(409, "IDEMPOTENCY_KEY_REUSED", "Reused."))).toBe(false);
    expect(keepsSubmission(api(403, "FORBIDDEN", "Not allowed."))).toBe(false);
    // Says nothing about the write: the same body goes out again with the same key.
    expect(keepsSubmission(api(429, "RATE_LIMITED", "Too many attempts."))).toBe(true);
    expect(keepsSubmission(api(500, "INTERNAL_ERROR", "Something went wrong."))).toBe(true);
    expect(keepsSubmission(api(503, "SERVICE_UNAVAILABLE", "Unavailable."))).toBe(true);
    expect(keepsSubmission(toApiError(502, "<html>"))).toBe(true);
    expect(keepsSubmission(ApiError.portal())).toBe(true);
    expect(keepsSubmission(ApiError.network())).toBe(true);
    expect(keepsSubmission(ApiError.unavailable(0))).toBe(true);
  });
});

describe("reading details", () => {
  test("staleQuote gives the quote of a QUOTE_STALE answer and nothing otherwise", () => {
    const fresh = quote({ shares: "44.9", quote_version: 9 });

    expect(staleQuote(api(409, "QUOTE_STALE", "The price moved.", { quote: fresh }))).toEqual(
      fresh,
    );
    expect(staleQuote(api(409, "QUOTE_STALE", "The price moved."))).toBeNull();
    expect(staleQuote(api(409, "CONFLICT", "No.", { quote: fresh }))).toBeNull();
  });

  test("retryAfterSeconds reads a RATE_LIMITED answer", () => {
    const limited = api(429, "RATE_LIMITED", "Too many attempts.", { retry_after_seconds: 30 });

    expect(retryAfterSeconds(limited)).toBe(30);
    expect(retryAfterSeconds(api(429, "RATE_LIMITED", "Too many attempts."))).toBeNull();
    expect(
      retryAfterSeconds(api(503, "SERVICE_UNAVAILABLE", "x", { retry_after_seconds: 30 })),
    ).toBeNull();
  });

  test("stakeLeft gives the two amounts of a stake-limit answer as the strings it sent", () => {
    const exceeded = api(422, "LIMIT_EXCEEDED", "This would exceed a limit.", {
      limit: "MAX_STAKE_PER_USER_MARKET",
      stake_limit_cr: "100",
      stake_left_cr: "12.5",
    });

    expect(stakeLeft(exceeded)).toEqual({ stake_left_cr: "12.5", stake_limit_cr: "100" });
    expect(limitName(exceeded)).toBe("MAX_STAKE_PER_USER_MARKET");
    expect(
      stakeLeft(api(422, "LIMIT_EXCEEDED", "x", { limit: "MAX_PROPOSALS_PER_USER_PER_DAY" })),
    ).toBeNull();
    expect(stakeLeft(api(422, "TRADE_TOO_SMALL", "x", { stake_left_cr: "1" }))).toBeNull();
  });

  test("portalIdentity gives who the portal says is calling; the name may be empty", () => {
    const required = api(403, "PROFILE_REQUIRED", "Set up your profile to continue.", {
      name: "",
      email: "anna.muster@ethz.ch",
    });

    expect(portalIdentity(required)).toEqual({ name: "", email: "anna.muster@ethz.ch" });
    expect(portalIdentity(api(403, "FORBIDDEN", "No."))).toBeNull();
  });
});

describe("placing an error on a form", () => {
  test("a rule a service checks names its field in details.field", () => {
    const taken = api(409, "USERNAME_TAKEN", "This username is taken.", { field: "username" });

    expect(fieldErrors(taken)).toEqual({ username: "This username is taken." });
    expect(placeError(taken, ["username", "degree_level"])).toEqual({
      fields: { username: "This username is taken." },
      form: null,
    });
  });

  test("a request that does not match its schema names fields by the last element of loc", () => {
    const invalid = api(422, "VALIDATION_ERROR", "The request is not valid.", {
      errors: [
        { loc: ["body", "question"], msg: "String should have at least 10 characters", type: "x" },
        { loc: ["body", "close_at"], msg: "Input should be a valid datetime", type: "y" },
        {
          loc: ["query", "min_win_rate"],
          msg: "Input should be less than or equal to 1",
          type: "z",
        },
      ],
    });

    expect(fieldErrors(invalid)).toEqual({
      question: "String should have at least 10 characters",
      close_at: "Input should be a valid datetime",
      min_win_rate: "Input should be less than or equal to 1",
    });
  });

  test("an error that names none of the form's fields goes above the submit button", () => {
    const exceeded = api(422, "LIMIT_EXCEEDED", "You can propose 3 markets a day.", {
      limit: "MAX_PROPOSALS_PER_USER_PER_DAY",
    });
    const elsewhere = api(422, "VALIDATION_ERROR", "The request is not valid.", {
      errors: [{ loc: ["body"], msg: "Input should be a valid dictionary", type: "x" }],
    });

    expect(placeError(exceeded, ["question"])).toEqual({
      fields: {},
      form: "You can propose 3 markets a day.",
    });
    expect(placeError(elsewhere, ["question"])).toEqual({
      fields: {},
      form: "The request is not valid.",
    });
    expect(placeError(ApiError.network(), ["question"]).form).toBe("No connection. Try again.");
  });
});
