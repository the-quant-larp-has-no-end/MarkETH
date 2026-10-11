import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import type { ApiError } from "@/lib/api/errors";
import type { BroadcastConfig } from "@/lib/api/schema";
import { page } from "@/lib/browser";
import { installFakeCapture, piece, pieceOfSize } from "@/test/capture";
import { BROADCAST_ID, broadcastChunk, broadcastConfig } from "@/test/fixtures";
import {
  apiError,
  deferred,
  errorPage,
  json,
  mockApi,
  type MockRequest,
  noAnswer,
  portal401,
} from "@/test/http";

import { Uploader, type UploadNotice } from "./uploader";

const UPLOAD = "POST /api/broadcasts/{broadcast_id}/chunks";
const WINDOW = broadcastConfig().restart_window_ms;

/** The server takes the piece and waits for the next place of its part. */
const taken = (request: MockRequest) =>
  json(200, broadcastChunk({ next_seq: Number(request.query.get("seq")) + 1 }));
const restart = () => json(200, broadcastChunk({ restart: true, next_seq: 0 }));

const made: Uploader[] = [];

function upload(overrides: Partial<BroadcastConfig> = {}) {
  const events: string[] = [];
  const notices: UploadNotice[] = [];
  const refusals: ApiError[] = [];
  const uploader = new Uploader({
    broadcastId: BROADCAST_ID,
    config: broadcastConfig(overrides),
    onPart: (reason) => events.push(`part:${reason}`),
    onPause: () => events.push("pause"),
    onStop: (error) => {
      refusals.push(error);
      events.push("stop");
    },
    onNotice: (notice) => notices.push(notice),
  });
  made.push(uploader);
  return { uploader, events, notices, refusals };
}

/** What a request said about its piece: the part, the place and the bytes as text. */
const sent = (request: MockRequest | undefined) => ({
  part: request?.query.get("part"),
  seq: request?.query.get("seq"),
  body: request?.rawBody,
});

/** Lets what is on its way arrive: the mock answers in real time, whatever the clock is. */
const arrive = async () => {
  for (let turn = 0; turn < 5; turn += 1) {
    await new Promise<void>((resolve) => setImmediate(resolve));
  }
};

beforeEach(() => {
  // The pieces of a test are Node's blobs, and so is what the uploader joins them into.
  installFakeCapture();
});

afterEach(() => {
  for (const uploader of made.splice(0)) uploader.stop();
  vi.useRealTimers();
});

describe("one request is out at a time", () => {
  test("the pieces that arrive meanwhile wait, and go up together with the next request", async () => {
    const first = deferred();
    const api = mockApi({ [UPLOAD]: first.handler });
    const { uploader } = upload();

    uploader.push(piece("one"));
    uploader.push(piece("two"));
    uploader.push(piece("three"));
    await arrive();
    expect(api.calls(UPLOAD)).toHaveLength(1);

    api.on(UPLOAD, taken);
    first.resolve(json(200, broadcastChunk({ next_seq: 1 })));
    await vi.waitFor(() => expect(api.calls(UPLOAD)).toHaveLength(2));

    const [one, rest] = api.calls(UPLOAD).map(sent);
    expect(one).toMatchObject({ seq: "0", body: "one" });
    expect(rest).toMatchObject({ seq: "1", body: "twothree" });
    expect(rest?.part).toBe(one?.part);
  });

  test("it goes to the live stream of the start, under a part id the API takes", async () => {
    const api = mockApi({ [UPLOAD]: taken });
    const { uploader } = upload();

    uploader.push(piece("one"));
    await vi.waitFor(() => expect(api.calls(UPLOAD)).toHaveLength(1));

    const request = api.lastCall(UPLOAD);
    expect(request?.path).toBe(`/api/broadcasts/${BROADCAST_ID}/chunks`);
    expect(request?.query.get("part")).toMatch(/^[A-Za-z0-9_-]{8,36}$/);
    expect(request?.headers.get("Content-Type")).toBe("application/octet-stream");
  });

  test("the place counts from 0 and is the one the answer names", async () => {
    const api = mockApi({ [UPLOAD]: json(200, broadcastChunk({ next_seq: 7 })) });
    const { uploader } = upload();

    uploader.push(piece("one"));
    await vi.waitFor(() => expect(api.calls(UPLOAD)).toHaveLength(1));
    uploader.push(piece("two"));
    await vi.waitFor(() => expect(api.calls(UPLOAD)).toHaveLength(2));

    expect(api.calls(UPLOAD).map((request) => request.query.get("seq"))).toEqual(["0", "7"]);
  });

  test("an empty piece is dropped", async () => {
    const api = mockApi({ [UPLOAD]: taken });
    const { uploader } = upload();

    uploader.push(piece(""));
    await arrive();

    expect(api.requests).toHaveLength(0);
  });
});

describe("a request carries as much as chunk_max_bytes", () => {
  test("pieces are joined while they fit, and the next one waits for the request after", async () => {
    const api = mockApi({ [UPLOAD]: taken });
    const { uploader } = upload({ chunk_max_bytes: 10 });

    // The first goes out at once and alone; the others arrive while it is out.
    uploader.push(piece("aaaa"));
    uploader.push(piece("bbbbbb"));
    uploader.push(piece("cccc"));
    uploader.push(piece("dd"));
    await vi.waitFor(() => expect(api.calls(UPLOAD)).toHaveLength(3));

    expect(api.calls(UPLOAD).map((request) => request.rawBody)).toEqual([
      "aaaa",
      "bbbbbbcccc",
      "dd",
    ]);
  });

  test("a piece larger than that is cut, and its rest goes up next, in order", async () => {
    const api = mockApi({ [UPLOAD]: taken });
    const { uploader } = upload({ chunk_max_bytes: 4 });

    // The first piece of an MP4 recorder, and what a recorder hands over after a sleep.
    uploader.push(piece("abcdefghij"));
    await vi.waitFor(() => expect(api.calls(UPLOAD)).toHaveLength(3));

    expect(api.calls(UPLOAD).map(sent)).toMatchObject([
      { seq: "0", body: "abcd" },
      { seq: "1", body: "efgh" },
      { seq: "2", body: "ij" },
    ]);
  });
});

describe("an answer that says nothing about the piece", () => {
  test.each([
    ["an error of the server", () => apiError(503, "SERVICE_UNAVAILABLE", "Try again shortly.")],
    ["an error page of the proxy", () => errorPage(502)],
    ["no answer", () => noAnswer()],
  ])("%s: the same bytes go up again under the same place", async (_name, failure) => {
    let calls = 0;
    const api = mockApi({
      [UPLOAD]: (request) => {
        calls += 1;
        return calls === 1 ? failure() : taken(request);
      },
    });
    const { uploader, events } = upload({ upload_retry_ms: 20 });

    uploader.push(piece("one"));
    uploader.push(piece("two"));
    await vi.waitFor(() => expect(api.calls(UPLOAD)).toHaveLength(3));

    const [first, again, next] = api.calls(UPLOAD).map(sent);
    expect(again).toEqual(first);
    expect(first).toMatchObject({ seq: "0", body: "one" });
    // What arrived meanwhile was not put into the body that is sent again.
    expect(next).toMatchObject({ seq: "1", body: "two", part: first?.part });
    expect(events).toEqual([]);
  });

  test("it is sent again only after upload_retry_ms", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    let calls = 0;
    const api = mockApi({
      [UPLOAD]: (request) => {
        calls += 1;
        return calls === 1 ? errorPage(502) : taken(request);
      },
    });
    const { uploader } = upload({ upload_retry_ms: 1000 });

    uploader.push(piece("one"));
    await arrive();
    await vi.advanceTimersByTimeAsync(999);
    await arrive();
    expect(api.calls(UPLOAD)).toHaveLength(1);

    await vi.advanceTimersByTimeAsync(1);
    await arrive();
    expect(api.calls(UPLOAD)).toHaveLength(2);
  });

  test("a 429 is sent again after the wait it names", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    let calls = 0;
    const api = mockApi({
      [UPLOAD]: (request) => {
        calls += 1;
        return calls === 1
          ? apiError(429, "RATE_LIMITED", "Too many requests.", { retry_after_seconds: 3 })
          : taken(request);
      },
    });
    const { uploader, events } = upload({ upload_retry_ms: 1000 });

    uploader.push(piece("one"));
    await arrive();
    await vi.advanceTimersByTimeAsync(2999);
    await arrive();
    expect(api.calls(UPLOAD)).toHaveLength(1);

    await vi.advanceTimersByTimeAsync(1);
    await arrive();
    const [first, again] = api.calls(UPLOAD).map(sent);
    expect(again).toEqual(first);
    expect(events).toEqual([]);
  });

  test("no answer within upload_timeout_ms ends the request, and the same bytes go up again", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    const requests: { url: string; ended: boolean }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const request = input as Request;
      const seen = { url: request.url, ended: false };
      requests.push(seen);
      // The first request is answered by nobody; the one after it is taken.
      if (requests.length > 1) return Promise.resolve(json(200, broadcastChunk()));
      return new Promise<Response>((_resolve, reject) => {
        request.signal.addEventListener("abort", () => {
          seen.ended = true;
          reject(new DOMException("The request was ended.", "AbortError"));
        });
      });
    });
    const { uploader } = upload({ upload_timeout_ms: 10_000, upload_retry_ms: 1000 });

    uploader.push(piece("one"));
    await vi.advanceTimersByTimeAsync(9999);
    expect(requests).toHaveLength(1);
    expect(requests[0]?.ended).toBe(false);

    await vi.advanceTimersByTimeAsync(1);
    expect(requests[0]?.ended).toBe(true);
    await vi.advanceTimersByTimeAsync(1000);
    await arrive();

    expect(requests).toHaveLength(2);
    expect(requests[1]?.url).toBe(requests[0]?.url);
  });
});

describe("the portal's 401 on an upload", () => {
  test("is no answer: the same bytes go up again, the studio is told, and the page stays", async () => {
    const reload = vi.spyOn(page, "reload").mockImplementation(() => {});
    let calls = 0;
    const api = mockApi({
      [UPLOAD]: (request) => {
        calls += 1;
        return calls < 3 ? portal401() : taken(request);
      },
    });
    const { uploader, notices, events } = upload({ upload_retry_ms: 20 });

    uploader.push(piece("one"));
    await vi.waitFor(() => expect(api.calls(UPLOAD)).toHaveLength(3));
    await vi.waitFor(() => expect(notices).toHaveLength(2));

    const [first, second, third] = api.calls(UPLOAD).map(sent);
    expect(second).toEqual(first);
    expect(third).toEqual(first);
    // Told once that the sign-in has ended, and once that an upload was answered again.
    expect(notices).toEqual([
      { slow: false, signedOut: true },
      { slow: false, signedOut: false },
    ]);
    expect(events).toEqual([]);
    expect(reload).not.toHaveBeenCalled();
  });

  test("a request without an answer says nothing about the sign-in", async () => {
    const third = deferred();
    let calls = 0;
    const api = mockApi({
      [UPLOAD]: () => {
        calls += 1;
        if (calls === 1) return portal401();
        return calls === 2 ? noAnswer() : third.handler();
      },
    });
    const { uploader, notices } = upload({ upload_retry_ms: 20 });

    uploader.push(piece("one"));
    // The 401, then nothing at all: the third request is out and nobody has answered since.
    await vi.waitFor(() => expect(api.calls(UPLOAD)).toHaveLength(3));
    expect(notices).toEqual([{ slow: false, signedOut: true }]);

    third.resolve(json(200, broadcastChunk()));
    await vi.waitFor(() => expect(notices).toHaveLength(2));
    expect(notices.at(-1)).toEqual({ slow: false, signedOut: false });
  });
});

describe("another part begins", () => {
  test("when the answer says restart: another id, place 0, and what waited is dropped", async () => {
    const first = deferred();
    const api = mockApi({ [UPLOAD]: first.handler });
    const { uploader, events } = upload();

    uploader.push(piece("one"));
    uploader.push(piece("two"));
    await vi.waitFor(() => expect(api.calls(UPLOAD)).toHaveLength(1));
    api.on(UPLOAD, taken);
    first.resolve(restart());
    await vi.waitFor(() => expect(events).toEqual(["part:restart"]));

    // The recorder was started again: its first piece opens the part.
    uploader.push(piece("fresh"));
    await vi.waitFor(() => expect(api.calls(UPLOAD)).toHaveLength(2));

    const [before, after] = api.calls(UPLOAD).map(sent);
    expect(after).toMatchObject({ seq: "0", body: "fresh" });
    expect(after?.part).not.toBe(before?.part);
    expect(after?.part).toMatch(/^[A-Za-z0-9_-]{8,36}$/);
  });

  test("when more waits than upload_backlog_bytes, and the answer that arrives late belongs to nobody", async () => {
    const first = deferred();
    const api = mockApi({ [UPLOAD]: first.handler });
    const { uploader, events } = upload({ upload_backlog_bytes: 10 });

    uploader.push(pieceOfSize(4));
    await vi.waitFor(() => expect(api.calls(UPLOAD)).toHaveLength(1));
    uploader.push(pieceOfSize(4));
    expect(events).toEqual([]);
    uploader.push(pieceOfSize(4));
    expect(events).toEqual(["part:backlog"]);

    api.on(UPLOAD, taken);
    // The request of the part that is over is answered after all: place 5 means nothing now.
    first.resolve(json(200, broadcastChunk({ next_seq: 5 })));
    await arrive();
    uploader.push(piece("fresh"));
    await vi.waitFor(() => expect(api.calls(UPLOAD)).toHaveLength(2));

    const [before, after] = api.calls(UPLOAD).map(sent);
    expect(after).toMatchObject({ seq: "0", body: "fresh" });
    expect(after?.part).not.toBe(before?.part);
  });

  test("when the studio asks for one", async () => {
    const api = mockApi({ [UPLOAD]: taken });
    const { uploader, events } = upload();

    uploader.push(piece("one"));
    await vi.waitFor(() => expect(api.calls(UPLOAD)).toHaveLength(1));
    uploader.anotherPart("asked");
    uploader.push(piece("fresh"));
    await vi.waitFor(() => expect(api.calls(UPLOAD)).toHaveLength(2));

    const [before, after] = api.calls(UPLOAD).map(sent);
    expect(events).toEqual(["part:asked"]);
    expect(after).toMatchObject({ seq: "0", body: "fresh" });
    expect(after?.part).not.toBe(before?.part);
  });

  test("the request that is out is ended", async () => {
    const signals: AbortSignal[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      signals.push((input as Request).signal);
      return new Promise<Response>(() => {});
    });
    const { uploader } = upload({ upload_backlog_bytes: 10 });

    uploader.push(pieceOfSize(6));
    await arrive();
    expect(signals[0]?.aborted).toBe(false);

    uploader.push(pieceOfSize(6));

    expect(signals[0]?.aborted).toBe(true);
  });
});

describe("the studio is told that the connection is slow", () => {
  test("while more than half of upload_backlog_bytes waits, and told again when it caught up", async () => {
    const first = deferred();
    const api = mockApi({ [UPLOAD]: first.handler });
    const { uploader, notices } = upload({ upload_backlog_bytes: 10 });

    uploader.push(pieceOfSize(4));
    await vi.waitFor(() => expect(api.calls(UPLOAD)).toHaveLength(1));
    expect(notices).toEqual([]);
    uploader.push(pieceOfSize(2));
    expect(notices).toEqual([{ slow: true, signedOut: false }]);

    api.on(UPLOAD, taken);
    first.resolve(json(200, broadcastChunk({ next_seq: 1 })));

    await vi.waitFor(() => expect(notices).toHaveLength(2));
    expect(notices.at(-1)).toEqual({ slow: false, signedOut: false });
  });
});

describe("three parts within restart_window_ms pause the studio", () => {
  test("the third time the upload has to start over, no fourth part begins and nothing more is sent", async () => {
    const api = mockApi({ [UPLOAD]: restart });
    const { uploader, events } = upload();

    uploader.push(piece("one"));
    await vi.waitFor(() => expect(events).toEqual(["part:restart"]));
    uploader.push(piece("two"));
    await vi.waitFor(() => expect(events).toEqual(["part:restart", "part:restart"]));
    uploader.push(piece("three"));
    await vi.waitFor(() => expect(events).toEqual(["part:restart", "part:restart", "pause"]));

    uploader.push(piece("four"));
    await arrive();
    expect(api.calls(UPLOAD)).toHaveLength(3);
    // Each part went up under an id of its own, from place 0.
    expect(new Set(api.calls(UPLOAD).map((request) => request.query.get("part"))).size).toBe(3);
    expect(api.calls(UPLOAD).map((request) => request.query.get("seq"))).toEqual(["0", "0", "0"]);
  });

  test("a part that began before the window does not count", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    mockApi({ [UPLOAD]: restart });
    const { uploader, events } = upload();

    uploader.push(piece("one"));
    await vi.waitFor(() => expect(events).toHaveLength(1));
    uploader.push(piece("two"));
    await vi.waitFor(() => expect(events).toHaveLength(2));

    vi.setSystemTime(Date.now() + WINDOW);
    uploader.push(piece("three"));
    await vi.waitFor(() => expect(events).toHaveLength(3));

    expect(events).toEqual(["part:restart", "part:restart", "part:restart"]);
  });
});

describe("what does not count against the three", () => {
  test("a part the studio asks for: a picture may change its shape as often as it does", async () => {
    mockApi({ [UPLOAD]: taken });
    const { uploader, events } = upload();

    for (let turn = 0; turn < 5; turn += 1) uploader.anotherPart("asked");

    expect(events).toEqual(["part:asked", "part:asked", "part:asked", "part:asked", "part:asked"]);
  });

  test("a part after a silent recorder: the upload may start over twice more before the third pauses it", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    mockApi({ [UPLOAD]: restart });
    const { uploader, events } = upload();

    await vi.advanceTimersByTimeAsync(WINDOW);
    expect(events).toEqual(["part:gap"]);

    for (const text of ["one", "two", "three"]) {
      uploader.push(piece(text));
      await arrive();
    }
    expect(events).toEqual(["part:gap", "part:restart", "part:restart", "part:restart"]);

    uploader.push(piece("four"));
    await arrive();
    expect(events.at(-1)).toBe("pause");
  });
});

describe("the API refuses a piece", () => {
  test.each([
    [409, "CONFLICT", "This live stream has ended."],
    [403, "FORBIDDEN", "This live stream is not yours to send."],
    [404, "NOT_FOUND", "There is no such live stream."],
  ] as const)(
    "%i stops the upload for good, with the API's sentence",
    async (status, code, message) => {
      const api = mockApi({ [UPLOAD]: apiError(status, code, message) });
      const { uploader, events, refusals } = upload();

      uploader.push(piece("one"));
      uploader.push(piece("two"));
      await vi.waitFor(() => expect(events).toEqual(["stop"]));
      uploader.push(piece("three"));
      await arrive();

      expect(refusals[0]?.message).toBe(message);
      expect(refusals[0]?.code).toBe(code);
      expect(api.calls(UPLOAD)).toHaveLength(1);
    },
  );
});

describe("a recorder that falls silent", () => {
  test("is started again when no piece arrived for restart_window_ms, as often as it takes", async () => {
    vi.useFakeTimers();
    const { events } = upload();

    await vi.advanceTimersByTimeAsync(WINDOW - 1);
    expect(events).toEqual([]);
    await vi.advanceTimersByTimeAsync(1);
    expect(events).toEqual(["part:gap"]);

    // Such a part does not count against the window: the studio does not pause for it.
    await vi.advanceTimersByTimeAsync(WINDOW * 4);
    expect(events).toEqual(["part:gap", "part:gap", "part:gap", "part:gap", "part:gap"]);
  });

  test("every piece that arrives counts the silence anew", async () => {
    vi.useFakeTimers();
    vi.spyOn(globalThis, "fetch").mockImplementation(() => new Promise<Response>(() => {}));
    const { uploader, events } = upload();

    await vi.advanceTimersByTimeAsync(WINDOW - 1000);
    uploader.push(piece("one"));
    await vi.advanceTimersByTimeAsync(WINDOW - 1);
    expect(events).toEqual([]);

    await vi.advanceTimersByTimeAsync(1);
    expect(events).toEqual(["part:gap"]);
  });
});

describe("stop", () => {
  test("ends the request that is out, sends nothing more and listens to nothing", async () => {
    vi.useFakeTimers();
    const signals: AbortSignal[] = [];
    const fetched = vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      signals.push((input as Request).signal);
      return new Promise<Response>(() => {});
    });
    const { uploader, events } = upload();

    uploader.push(piece("one"));
    await vi.advanceTimersByTimeAsync(0);
    expect(signals[0]?.aborted).toBe(false);

    uploader.stop();

    // At once, and not when the time of the request is up.
    expect(signals[0]?.aborted).toBe(true);
    uploader.push(piece("two"));
    uploader.anotherPart("asked");
    await vi.advanceTimersByTimeAsync(WINDOW * 2);
    expect(fetched).toHaveBeenCalledTimes(1);
    expect(events).toEqual([]);
  });
});
