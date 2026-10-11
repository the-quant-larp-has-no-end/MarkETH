import { describe, expect, test, vi } from "vitest";

import { page } from "@/lib/browser";
import { piece } from "@/test/capture";
import { BROADCAST_ID, broadcastChunk, config } from "@/test/fixtures";
import { apiError, errorPage, json, mockApi, noAnswer, portal401 } from "@/test/http";

import { type BroadcastChunk, sendBroadcastChunk } from "./broadcast-upload";
import { NETWORK_MESSAGE, PORTAL_MESSAGE, UNAVAILABLE_MESSAGE } from "./errors";

const UPLOAD = "POST /api/broadcasts/{broadcast_id}/chunks";
const TIMEOUT = config().broadcast.upload_timeout_ms;

const chunk = (overrides: Partial<BroadcastChunk> = {}): BroadcastChunk => ({
  broadcastId: BROADCAST_ID,
  part: "part-0001",
  seq: 4,
  body: piece("one second of video"),
  timeoutMs: TIMEOUT,
  ...overrides,
});

/** A request that is answered only by being ended, as a browser's is. */
function hangUntilEnded() {
  return vi.spyOn(globalThis, "fetch").mockImplementation(
    (input) =>
      new Promise<Response>((_resolve, reject) => {
        const { signal } = input as Request;
        const end = () => reject(new DOMException("The request was ended.", "AbortError"));
        if (signal.aborted) end();
        signal.addEventListener("abort", end);
      }),
  );
}

describe("a piece of a live stream goes up", () => {
  test("as its bytes, with its part and its place in the address, asking for JSON", async () => {
    const api = mockApi({ [UPLOAD]: json(200, broadcastChunk({ next_seq: 5 })) });

    const answer = await sendBroadcastChunk(chunk());

    expect(answer).toEqual({ outcome: "ok", nextSeq: 5 });
    const [sent] = api.calls(UPLOAD);
    expect(api.requests).toHaveLength(1);
    expect(sent?.path).toBe(`/api/broadcasts/${BROADCAST_ID}/chunks`);
    expect(sent?.query.toString()).toBe("part=part-0001&seq=4");
    expect(sent?.rawBody).toBe("one second of video");
    expect(sent?.headers.get("Content-Type")).toBe("application/octet-stream");
    // What makes the portal answer with a 401 and not with a redirect to its sign-in.
    expect(sent?.headers.get("Accept")).toBe("application/json");
  });

  test("a second time with the same bytes under the same place", async () => {
    const api = mockApi({ [UPLOAD]: json(200, broadcastChunk()) });
    const same = chunk();

    await sendBroadcastChunk(same);
    await sendBroadcastChunk(same);

    const [first, second] = api.calls(UPLOAD);
    expect(second?.rawBody).toBe(first?.rawBody);
    expect(second?.url).toBe(first?.url);
    expect(second?.rawBody).toBe("one second of video");
  });

  test("and an answer that says restart begins another part", async () => {
    mockApi({ [UPLOAD]: json(200, broadcastChunk({ restart: true, next_seq: 0 })) });

    expect(await sendBroadcastChunk(chunk())).toEqual({ outcome: "restart" });
  });
});

describe("the portal's 401 on an upload", () => {
  test("is reported, and the page is not reloaded: the camera and the stream stay", async () => {
    const reload = vi.spyOn(page, "reload").mockImplementation(() => {});
    mockApi({ [UPLOAD]: portal401() });

    const answer = await sendBroadcastChunk(chunk());

    expect(answer).toMatchObject({ outcome: "repeat", signedOut: true, retryAfterSeconds: null });
    expect(answer.outcome === "repeat" && answer.error.kind).toBe("portal");
    expect(answer.outcome === "repeat" && answer.error.message).toBe(PORTAL_MESSAGE);
    expect(reload).not.toHaveBeenCalled();
    // The one reload the page has for the portal's 401 is not used up either.
    expect(window.sessionStorage).toHaveLength(0);
  });

  test("goes with the next upload that is answered", async () => {
    vi.spyOn(page, "reload").mockImplementation(() => {});
    const api = mockApi({ [UPLOAD]: portal401() });
    const same = chunk();

    const refused = await sendBroadcastChunk(same);
    api.on(UPLOAD, json(200, broadcastChunk({ next_seq: 5 })));
    const taken = await sendBroadcastChunk(same);

    expect(refused.outcome).toBe("repeat");
    expect(taken).toEqual({ outcome: "ok", nextSeq: 5 });
  });

  test("differs from a 401 of the API, which is a refusal", async () => {
    mockApi({
      [UPLOAD]: apiError(401, "UNAUTHENTICATED", "Sign in through the ETH portal to continue."),
    });

    const answer = await sendBroadcastChunk(chunk());

    expect(answer).toMatchObject({ outcome: "stop" });
    expect(answer.outcome === "stop" && answer.error.message).toBe(
      "Sign in through the ETH portal to continue.",
    );
  });
});

describe("an upload nobody can say the outcome of is sent again", () => {
  test.each([
    [
      "an error of the server",
      () =>
        apiError(
          503,
          "SERVICE_UNAVAILABLE",
          "Live video is unavailable right now. Try again shortly.",
        ),
      "Live video is unavailable right now. Try again shortly.",
    ],
    ["an error page of the proxy", () => errorPage(502), UNAVAILABLE_MESSAGE],
    [
      "a page that is not the API's answer",
      () => new Response("<html></html>"),
      UNAVAILABLE_MESSAGE,
    ],
    ["an empty answer", () => new Response(null, { status: 204 }), UNAVAILABLE_MESSAGE],
    ["a 200 with JSON that is not the API's answer", () => json(200, {}), UNAVAILABLE_MESSAGE],
    ["no answer", () => noAnswer(), NETWORK_MESSAGE],
  ])("%s", async (_name, reply, message) => {
    const reload = vi.spyOn(page, "reload").mockImplementation(() => {});
    mockApi({ [UPLOAD]: () => reply() });

    const answer = await sendBroadcastChunk(chunk());

    expect(answer).toMatchObject({ outcome: "repeat", signedOut: false, retryAfterSeconds: null });
    expect(answer.outcome === "repeat" && answer.error.message).toBe(message);
    expect(reload).not.toHaveBeenCalled();
  });

  test("after the wait a 429 asks for", async () => {
    mockApi({
      [UPLOAD]: apiError(429, "RATE_LIMITED", "Too many attempts. Try again later.", {
        retry_after_seconds: 7,
      }),
    });

    expect(await sendBroadcastChunk(chunk())).toMatchObject({
      outcome: "repeat",
      signedOut: false,
      retryAfterSeconds: 7,
    });
  });

  test("when no answer came in the time an upload may take: the request is ended then", async () => {
    vi.useFakeTimers();
    try {
      const fetched = hangUntilEnded();
      const settled = vi.fn();
      const sending = sendBroadcastChunk(chunk()).then((answer) => {
        settled(answer);
        return answer;
      });

      await vi.advanceTimersByTimeAsync(TIMEOUT - 1);
      expect(settled).not.toHaveBeenCalled();
      await vi.advanceTimersByTimeAsync(1);

      expect(await sending).toMatchObject({ outcome: "repeat", signedOut: false });
      expect((fetched.mock.calls[0]![0] as Request).signal.aborted).toBe(true);
      expect(vi.getTimerCount()).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });

  test("and a request the studio ends itself comes back the same way, with nothing left waiting", async () => {
    vi.useFakeTimers();
    try {
      hangUntilEnded();
      const studio = new AbortController();
      const sending = sendBroadcastChunk(chunk({ signal: studio.signal }));

      studio.abort();

      const answer = await sending;
      expect(answer).toMatchObject({ outcome: "repeat", signedOut: false });
      expect(answer.outcome === "repeat" && answer.error.kind).toBe("network");
      expect(vi.getTimerCount()).toBe(0);

      // One that was ended before it went out is not sent at all.
      const fetched = hangUntilEnded();
      expect(await sendBroadcastChunk(chunk({ signal: studio.signal }))).toMatchObject({
        outcome: "repeat",
      });
      expect((fetched.mock.calls[0]![0] as Request).signal.aborted).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("an upload the API refuses stops the uploader for good", () => {
  test("with the sentence and the details of the API: the live stream has ended", async () => {
    mockApi({
      [UPLOAD]: apiError(409, "CONFLICT", "This live stream has ended.", {
        status: "ENDED",
        end_reason: "STALLED",
      }),
    });

    const answer = await sendBroadcastChunk(chunk());

    expect(answer).toMatchObject({ outcome: "stop" });
    expect(answer.outcome === "stop" && answer.error.message).toBe("This live stream has ended.");
    expect(answer.outcome === "stop" && answer.error.details).toEqual({
      status: "ENDED",
      end_reason: "STALLED",
    });
  });

  test.each([
    [403, "FORBIDDEN", "This account is suspended."],
    [403, "FORBIDDEN", "Only the person this live stream belongs to can do this."],
    [404, "NOT_FOUND", "Not found."],
    [409, "CONFLICT", "This live stream has not started."],
    [413, "VALIDATION_ERROR", "The request is too large."],
    [422, "VALIDATION_ERROR", "The request has no video data."],
  ] as const)("%i %s: %s", async (status, code, message) => {
    const reload = vi.spyOn(page, "reload").mockImplementation(() => {});
    mockApi({ [UPLOAD]: apiError(status, code, message) });

    const answer = await sendBroadcastChunk(chunk());

    expect(answer.outcome).toBe("stop");
    expect(answer.outcome === "stop" && answer.error.message).toBe(message);
    expect(reload).not.toHaveBeenCalled();
  });
});
