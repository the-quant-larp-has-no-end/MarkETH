import { beforeEach, describe, expect, test, vi } from "vitest";

import { me } from "@/test/fixtures";
import { apiError, errorPage, json, mockApi, noAnswer, portal401 } from "@/test/http";

import { page } from "../browser";
import { call } from "./client";
import { ApiError, NETWORK_MESSAGE, PORTAL_MESSAGE, UNAVAILABLE_MESSAGE } from "./errors";

async function failure(run: () => Promise<unknown>): Promise<ApiError> {
  try {
    await run();
  } catch (error) {
    if (error instanceof ApiError) return error;
    throw error;
  }
  throw new Error("The call did not fail.");
}

const getMe = () => call((api) => api.GET("/api/me"));

beforeEach(() => {
  vi.spyOn(page, "reload").mockImplementation(() => {});
});

describe("a call", () => {
  test("goes to /api on the page's own origin and asks for JSON", async () => {
    const api = mockApi({ "GET /api/me": json(200, me()) });

    await getMe();

    const [request] = api.requests;
    expect(request?.url).toBe(`${window.location.origin}/api/me`);
    expect(request?.headers.get("Accept")).toBe("application/json");
  });

  test("returns the body of a 2xx answer", async () => {
    mockApi({ "GET /api/me": json(200, me({ username: "polyterrasse", balance_cr: "1250.5" })) });

    const answer = await getMe();

    expect(answer.username).toBe("polyterrasse");
    expect(answer.balance_cr).toBe("1250.5");
  });

  test("sends a body as JSON and a repeated query parameter once per value", async () => {
    const api = mockApi({
      "POST /api/codes/redeem": json(200, { value_cr: "35", balance_cr: "275" }),
      "GET /api/markets": json(200, { items: [] }),
    });

    await call((client) => client.POST("/api/codes/redeem", { body: { code: "ABCD-EFGH-JKMN" } }));
    await call((client) =>
      client.GET("/api/markets", { params: { query: { status: ["OPEN", "HALTED"] } } }),
    );

    const redeem = api.lastCall("POST /api/codes/redeem");
    expect(redeem?.body).toEqual({ code: "ABCD-EFGH-JKMN" });
    expect(redeem?.headers.get("Content-Type")).toBe("application/json");
    expect(api.lastCall("GET /api/markets")?.query.getAll("status")).toEqual(["OPEN", "HALTED"]);
  });
});

describe("a failed call", () => {
  test("keeps the API's code, message and details as sent", async () => {
    mockApi({
      "GET /api/me": apiError(403, "PROFILE_REQUIRED", "Set up your profile to continue.", {
        name: "Anna Muster",
        email: "anna.muster@ethz.ch",
      }),
    });

    const error = await failure(getMe);

    expect(error.kind).toBe("api");
    expect(error.status).toBe(403);
    expect(error.code).toBe("PROFILE_REQUIRED");
    expect(error.message).toBe("Set up your profile to continue.");
    expect(error.details).toEqual({ name: "Anna Muster", email: "anna.muster@ethz.ch" });
  });

  test("a 401 with a code is the API's and reloads nothing", async () => {
    mockApi({
      "GET /api/me": apiError(
        401,
        "UNAUTHENTICATED",
        "Sign in through the ETH portal to continue.",
      ),
    });

    const error = await failure(getMe);

    expect(error.kind).toBe("api");
    expect(error.code).toBe("UNAUTHENTICATED");
    expect(error.message).toBe("Sign in through the ETH portal to continue.");
    expect(page.reload).not.toHaveBeenCalled();
  });

  test("the portal's 401 with the body {} reloads the page, once", async () => {
    mockApi({ "GET /api/me": portal401() });

    const first = await failure(getMe);

    expect(first.kind).toBe("portal");
    expect(first.code).toBeNull();
    expect(first.message).toBe(PORTAL_MESSAGE);
    expect(first.recovering).toBe(true);
    expect(page.reload).toHaveBeenCalledTimes(1);

    // The reload brought the same answer: the page stays and start-up shows the message.
    const second = await failure(getMe);

    expect(second.kind).toBe("portal");
    expect(second.recovering).toBe(false);
    expect(page.reload).toHaveBeenCalledTimes(1);
  });

  test("a 401 with an empty body is the portal's too", async () => {
    mockApi({ "GET /api/me": new Response(null, { status: 401 }) });

    const error = await failure(getMe);

    expect(error.kind).toBe("portal");
    expect(page.reload).toHaveBeenCalledTimes(1);
  });

  test("an error page of the proxy counts as temporarily unavailable", async () => {
    mockApi({ "GET /api/me": errorPage(502) });

    const error = await failure(getMe);

    expect(error.kind).toBe("unavailable");
    expect(error.status).toBe(502);
    expect(error.code).toBe("SERVICE_UNAVAILABLE");
    expect(error.message).toBe(UNAVAILABLE_MESSAGE);
    expect(page.reload).not.toHaveBeenCalled();
  });

  test("a JSON answer without a code counts as temporarily unavailable", async () => {
    mockApi({ "GET /api/me": json(404, { detail: "Not Found" }) });

    const error = await failure(getMe);

    expect(error.kind).toBe("unavailable");
    expect(error.message).toBe(UNAVAILABLE_MESSAGE);
  });

  test("a 2xx that is not JSON counts as temporarily unavailable", async () => {
    mockApi({
      "GET /api/me": new Response("<html>Sign in</html>", {
        status: 200,
        headers: { "Content-Type": "text/html" },
      }),
    });

    const error = await failure(getMe);

    expect(error.kind).toBe("unavailable");
    expect(error.outcomeUnknown).toBe(true);
  });

  test("no response is a network failure", async () => {
    mockApi({ "GET /api/me": noAnswer });

    const error = await failure(getMe);

    expect(error.kind).toBe("network");
    expect(error.status).toBe(0);
    expect(error.code).toBeNull();
    expect(error.message).toBe(NETWORK_MESSAGE);
  });
});
