import { act, screen, waitFor, within } from "@testing-library/react";
import type { UserEvent } from "@testing-library/user-event";
import { afterEach, describe, expect, test, vi } from "vitest";

import { AppShell } from "@/components/shell";
import type {
  BroadcastConfig,
  BroadcastItem,
  BroadcastStatus,
  BroadcastViewer,
  MarketBroadcastResponse,
} from "@/lib/api/schema";
import { page } from "@/lib/browser";
import {
  type FakeCapture,
  type FakeCaptureOptions,
  installFakeCapture,
  piece,
  pieceOfSize,
  showPicture,
} from "@/test/capture";
import { installFakeEventSource } from "@/test/fake-event-source";
import {
  BROADCAST_ID,
  broadcastChangedEvent,
  broadcastChunk,
  broadcastConfig,
  broadcastIn,
  broadcastViewer,
  config,
  MARKET_ID,
  marketBroadcast,
  marketDetail,
  noBroadcast,
  OTHER_MARKET_ID,
  readyEvent,
} from "@/test/fixtures";
import {
  apiError,
  deferred,
  type Handler,
  json,
  mockApi,
  type MockRequest,
  portal401,
  type Route,
} from "@/test/http";
import { renderWithProviders } from "@/test/render";

import { BroadcastStudioScreen } from "./broadcast-studio-screen";

const BROADCAST: Route = "GET /api/markets/{market_id}/broadcast";
const MARKET: Route = "GET /api/markets/{market_id}";
const REQUEST: Route = "POST /api/markets/{market_id}/broadcast/request";
const START: Route = "POST /api/broadcasts/{broadcast_id}/start";
const START_AS_ADMIN: Route = "POST /api/admin/markets/{market_id}/broadcast";
const UPLOAD: Route = "POST /api/broadcasts/{broadcast_id}/chunks";
const END: Route = "POST /api/broadcasts/{broadcast_id}/end";

/** The id of the live stream a start answers with, where it is another one than was started. */
const NEXT_BROADCAST_ID = "0f9e8d7c-6b5a-4c3d-9e2f-1a0b9c8d7e6f";

/** A live stream of the caller's own, in one status. */
const own = (
  status: BroadcastStatus,
  fields: Partial<BroadcastItem> = {},
  viewer: Partial<BroadcastViewer> = {},
): MarketBroadcastResponse =>
  marketBroadcast({
    broadcast: broadcastIn(status, fields),
    viewer: broadcastViewer({ is_broadcaster: true, can_watch: false, ...viewer }),
  });

/** An admin on a market without a live stream. */
const adminMayStart = () => noBroadcast({ can_start_as_admin: true });
/** The caller is on air: what a start answers, and what the studio reads while it sends. */
const onAir = (fields: Partial<BroadcastItem> = {}) =>
  own("LIVE", { on_air: false, playlist_url: null, ...fields }, { can_end: true });

/** The server takes the piece and waits for the next place of its part. */
const taken = (request: MockRequest) =>
  json(200, broadcastChunk({ next_seq: Number(request.query.get("seq")) + 1 }));

const tabBar = () => screen.queryByRole("navigation", { name: "Main" });
const preview = () => screen.queryByLabelText("Preview");
const uploads = (api: ReturnType<typeof mockApi>) =>
  api.calls(UPLOAD).map((request) => ({
    broadcast: request.path.split("/")[3],
    part: request.query.get("part"),
    seq: request.query.get("seq"),
    body: request.rawBody,
  }));

type Setup = {
  /** What `GET /markets/{id}/broadcast` answers. */
  answer: MarketBroadcastResponse | Handler;
  routes?: Partial<Record<Route, Handler>>;
  capture?: FakeCaptureOptions;
  broadcast?: Partial<BroadcastConfig>;
  live?: boolean;
};

function renderStudio({ answer, routes = {}, capture: options, broadcast, live }: Setup) {
  const capture = installFakeCapture(options);
  const api = mockApi({
    [BROADCAST]:
      answer instanceof Response || typeof answer === "function" ? answer : json(200, answer),
    [MARKET]: json(200, marketDetail()),
    [UPLOAD]: taken,
    ...routes,
  });
  const view = renderWithProviders(
    <AppShell>
      <BroadcastStudioScreen marketId={MARKET_ID} />
    </AppShell>,
    {
      live,
      // An announcement is read at once.
      config: config({
        live_announce_spread_ms: 0,
        broadcast: broadcastConfig({ upload_retry_ms: 20, ...broadcast }),
      }),
    },
  );
  return { api, capture, ...view };
}

/** The set-up has a picture, and its "Go live" is tapped. */
async function tapGoLive(user: UserEvent) {
  showPicture((await screen.findByLabelText("Preview")) as HTMLVideoElement);
  const button = screen.getByRole("button", { name: "Go live" });
  await waitFor(() => expect(button).toBeEnabled());
  await user.click(button);
}

/** An admin goes live from the set-up. */
async function goLiveAsAdmin(setup: Omit<Setup, "answer"> = {}) {
  const made = renderStudio({ answer: adminMayStart(), ...setup });
  made.api.on(START_AS_ADMIN, () => {
    // From here on the market has a live stream, and it is the caller's.
    made.api.on(BROADCAST, json(200, onAir()));
    return json(200, onAir());
  });
  await tapGoLive(made.user);
  await screen.findByRole("heading", { name: "You are live" });
  return made;
}

/** The recorder hands a piece over. */
const hand = (capture: FakeCapture, blob: Blob) => act(() => capture.recorder().emit(blob));

afterEach(() => {
  vi.useRealTimers();
});

describe("before the studio knows what the caller may do", () => {
  test("while the read loads: a skeleton in the shape of the preview and of the button, and no camera", () => {
    const { capture } = renderStudio({ answer: deferred().handler });

    expect(screen.getByRole("heading", { level: 1, name: "Live stream" })).toBeInTheDocument();
    expect(document.querySelectorAll('[data-slot="skeleton"]')).toHaveLength(2);
    expect(capture.requests).toEqual([]);
  });

  test("an error of the read: the API's message with 'Try again', which reads again", async () => {
    const { api, user } = renderStudio({
      answer: apiError(500, "INTERNAL_ERROR", "Something went wrong on our side."),
    });

    expect(await screen.findByText("Something went wrong on our side.")).toBeInTheDocument();
    api.on(BROADCAST, json(200, adminMayStart()));
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByRole("button", { name: "Go live" })).toBeInTheDocument();
  });

  test("a market the caller may not see is the 'not found' screen", async () => {
    renderStudio({ answer: apiError(404, "NOT_FOUND", "There is no such market.") });

    expect(await screen.findByText(/This page does not exist/)).toBeInTheDocument();
  });

  test("a caller without a flag gets the 'not found' screen, and is not asked for a camera", async () => {
    const { capture } = renderStudio({ answer: marketBroadcast() });

    expect(await screen.findByText(/This page does not exist/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Go live" })).not.toBeInTheDocument();
    expect(capture.requests).toEqual([]);
  });

  test("an admin who watches somebody else's live stream has no studio for it", async () => {
    renderStudio({
      answer: marketBroadcast({ viewer: broadcastViewer({ can_end: true }) }),
    });

    expect(await screen.findByText(/This page does not exist/)).toBeInTheDocument();
  });
});

describe("setting up", () => {
  test("the question, the preview, 'Go live' and the two notes with the figures of /config", async () => {
    const { capture } = renderStudio({
      answer: adminMayStart(),
      broadcast: { max_minutes: 90, stall_seconds: 45 },
    });

    const video = (await screen.findByLabelText("Preview")) as HTMLVideoElement;
    expect(screen.getByText(marketDetail().question)).toBeInTheDocument();
    // The broadcaster's own picture: muted, inline, and without controls.
    expect(video.muted).toBe(true);
    expect(video.controls).toBe(false);
    expect(video.srcObject).toBe(capture.stream());
    expect(
      screen.getByText(
        "Viewers see you a few seconds later. No picture and no sound is kept after it ends.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "It ends by itself after 90 minutes, or 45 seconds after the last picture arrived.",
      ),
    ).toBeInTheDocument();
    expect(tabBar()).toBeInTheDocument();
  });

  test("'Go live' is disabled until the preview shows a picture", async () => {
    renderStudio({ answer: adminMayStart() });

    const video = (await screen.findByLabelText("Preview")) as HTMLVideoElement;
    const button = screen.getByRole("button", { name: "Go live" });
    expect(button).toBeDisabled();

    showPicture(video);

    await waitFor(() => expect(button).toBeEnabled());
  });

  test("an admin's 'Go live' sends the admin's start, and the recorder starts only after the answer says LIVE", async () => {
    const start = deferred();
    const { api, capture, user } = renderStudio({
      answer: adminMayStart(),
      routes: { [START_AS_ADMIN]: start.handler },
    });

    await tapGoLive(user);
    await waitFor(() => expect(api.calls(START_AS_ADMIN)).toHaveLength(1));

    expect(api.lastCall(START_AS_ADMIN)?.path).toBe(`/api/admin/markets/${MARKET_ID}/broadcast`);
    expect(screen.getByRole("button", { name: "Go live" })).toBeDisabled();
    expect(capture.recorders).toHaveLength(0);

    start.resolve(json(200, onAir()));

    expect(await screen.findByRole("heading", { name: "You are live" })).toBeInTheDocument();
    expect(capture.recorders).toHaveLength(1);
    expect(capture.recorder().state).toBe("recording");
    expect(capture.recorder().timeslice).toBe(broadcastConfig().chunk_ms);
    expect(api.calls(START)).toHaveLength(0);
  });

  test("the broadcaster of an approved live stream sends its start, and the pieces go to the id of the answer", async () => {
    const { api, capture, user } = renderStudio({
      answer: own("APPROVED", {}, { can_start: true }),
      routes: { [START]: json(200, onAir({ id: NEXT_BROADCAST_ID })) },
    });

    await tapGoLive(user);
    await screen.findByRole("heading", { name: "You are live" });
    hand(capture, piece("one"));

    await waitFor(() => expect(api.calls(UPLOAD)).toHaveLength(1));
    expect(api.lastCall(START)?.path).toBe(`/api/broadcasts/${BROADCAST_ID}/start`);
    expect(api.calls(START_AS_ADMIN)).toHaveLength(0);
    expect(uploads(api)).toMatchObject([{ broadcast: NEXT_BROADCAST_ID, seq: "0", body: "one" }]);
  });

  test("a refusal stands above the button and keeps the preview; nothing is recorded", async () => {
    const { capture, user } = renderStudio({
      answer: adminMayStart(),
      routes: {
        [START_AS_ADMIN]: apiError(409, "LIMIT_EXCEEDED", "Another live stream is on right now."),
      },
    });

    await tapGoLive(user);

    expect(await screen.findByText("Another live stream is on right now.")).toBeInTheDocument();
    expect(preview()).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Go live" })).toBeEnabled();
    expect(capture.recorders).toHaveLength(0);
    expect(capture.stream().released).toBe(false);
    expect(screen.queryByRole("heading", { name: "You are live" })).not.toBeInTheDocument();
  });

  test("a refusal after which the market is not the caller's to start keeps its sentence on screen", async () => {
    const { api, user } = renderStudio({ answer: adminMayStart() });
    api.on(START_AS_ADMIN, () => {
      // The screen was behind: the market has a live stream of somebody else.
      api.on(BROADCAST, json(200, marketBroadcast()));
      return apiError(409, "CONFLICT", "This market has a live stream already.");
    });

    await tapGoLive(user);

    expect(await screen.findByText("This market has a live stream already.")).toBeInTheDocument();
    await waitFor(() => expect(api.calls(BROADCAST).length).toBeGreaterThan(1));
    await waitFor(() => expect(screen.getByRole("button", { name: "Go live" })).toBeDisabled());
    expect(screen.queryByText(/This page does not exist/)).not.toBeInTheDocument();
  });

  test("the part of the server that relays video does not answer: its sentence, and the set-up stays", async () => {
    const { user } = renderStudio({
      answer: adminMayStart(),
      routes: {
        [START_AS_ADMIN]: apiError(
          503,
          "SERVICE_UNAVAILABLE",
          "Live video is unavailable right now. Try again shortly.",
        ),
      },
    });

    await tapGoLive(user);

    expect(
      await screen.findByText("Live video is unavailable right now. Try again shortly."),
    ).toBeInTheDocument();
    expect(preview()).toBeInTheDocument();
  });

  test("another market is another studio: nothing of the one before stands in it", async () => {
    const { api, capture, rerender, user } = renderStudio({
      // The caller may start on this market, and on no other.
      answer: (request) =>
        json(200, request.path.includes(MARKET_ID) ? adminMayStart() : marketBroadcast()),
      routes: {
        [START_AS_ADMIN]: apiError(409, "LIMIT_EXCEEDED", "Another live stream is on right now."),
      },
    });
    await tapGoLive(user);
    await screen.findByText("Another live stream is on right now.");
    const first = capture.stream();

    rerender(
      <AppShell>
        <BroadcastStudioScreen marketId={OTHER_MARKET_ID} />
      </AppShell>,
    );

    expect(await screen.findByText(/This page does not exist/)).toBeInTheDocument();
    expect(screen.queryByText("Another live stream is on right now.")).toBeNull();
    expect(first.released).toBe(true);
    expect(api.lastCall(BROADCAST)?.path).toBe(`/api/markets/${OTHER_MARKET_ID}/broadcast`);
  });

  test("leaving the set-up lets the camera go", async () => {
    const { capture, unmount } = renderStudio({ answer: adminMayStart() });
    await screen.findByLabelText("Preview");

    unmount();

    expect(capture.stream().released).toBe(true);
  });
});

describe("what the browser refuses stands where the preview would", () => {
  test.each([
    [
      { access: "refused" },
      "Camera and microphone access was refused. Allow both in your browser settings.",
    ],
    [{ secure: false }, "The camera needs a secure connection (HTTPS)."],
    [{ access: "missing" }, "No camera or microphone is available."],
    [
      { supported: [] },
      "This browser cannot broadcast. Use a current Chrome, Edge, Firefox or Safari.",
    ],
    [
      { recorder: false },
      "This browser cannot broadcast. Use a current Chrome, Edge, Firefox or Safari.",
    ],
  ] as const)("%o: %s", async (options, sentence) => {
    renderStudio({ answer: adminMayStart(), capture: options });

    expect(await screen.findByText(sentence)).toBeInTheDocument();
    expect(preview()).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Go live" })).toBeDisabled();
  });

  test("a camera that goes away before the start: the banner, and no 'Go live'", async () => {
    const { capture } = renderStudio({ answer: adminMayStart() });
    showPicture((await screen.findByLabelText("Preview")) as HTMLVideoElement);

    act(() => capture.stream().video.end());

    expect(await screen.findByText("No camera or microphone is available.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Go live" })).toBeDisabled();
  });
});

describe("live", () => {
  test("the Live badge, 'You are live', since when, the preview and the banner; the tab bar is hidden", async () => {
    await goLiveAsAdmin();

    expect(screen.getByText("Live")).toBeInTheDocument();
    // The time of day alone, in the timezone of /config.
    expect(screen.getByText("11:45")).toHaveAttribute("datetime", "2026-10-11T09:45:00Z");
    expect(screen.getByText(/^Since/)).toHaveTextContent("Since 11:45");
    expect(preview()).toBeInTheDocument();
    expect(
      screen.getByText("Keep this page open and in front. The stream pauses while it is hidden."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "End stream" })).toBeInTheDocument();
    expect(screen.queryByText("Your connection is slow. Viewers may see pauses.")).toBeNull();
    expect(screen.queryByText(/Your ETH sign-in has ended/)).toBeNull();
    await waitFor(() => expect(tabBar()).not.toBeInTheDocument());
  });

  test("the pieces go up one after the other, each under the place the answer before it named", async () => {
    const { api, capture } = await goLiveAsAdmin();

    hand(capture, piece("one"));
    await waitFor(() => expect(api.calls(UPLOAD)).toHaveLength(1));
    hand(capture, piece("two"));
    await waitFor(() => expect(api.calls(UPLOAD)).toHaveLength(2));

    const [first, second] = uploads(api);
    expect(first).toMatchObject({ broadcast: BROADCAST_ID, seq: "0", body: "one" });
    expect(second).toMatchObject({ broadcast: BROADCAST_ID, seq: "1", body: "two" });
    expect(second?.part).toBe(first?.part);
  });

  test("a slow connection: the second banner while more than half of upload_backlog_bytes waits", async () => {
    const first = deferred();
    const { api, capture } = await goLiveAsAdmin({
      broadcast: { upload_backlog_bytes: 10 },
      routes: { [UPLOAD]: first.handler },
    });

    hand(capture, pieceOfSize(4));
    await waitFor(() => expect(api.calls(UPLOAD)).toHaveLength(1));
    hand(capture, pieceOfSize(2));

    expect(
      await screen.findByText("Your connection is slow. Viewers may see pauses."),
    ).toBeInTheDocument();

    api.on(UPLOAD, taken);
    first.resolve(json(200, broadcastChunk({ next_seq: 1 })));

    await waitFor(() =>
      expect(screen.queryByText("Your connection is slow. Viewers may see pauses.")).toBeNull(),
    );
  });

  test("the server does not take a piece: the recorder starts over, and what its ended run still hands over goes nowhere", async () => {
    let calls = 0;
    const { api, capture } = await goLiveAsAdmin({
      routes: {
        [UPLOAD]: (request) => {
          calls += 1;
          return calls === 1
            ? json(200, broadcastChunk({ restart: true, next_seq: 0 }))
            : taken(request);
        },
      },
    });
    const first = capture.recorder();

    hand(capture, piece("one"));
    await waitFor(() => expect(capture.recorders).toHaveLength(2));
    expect(first.state).toBe("inactive");
    expect(capture.recorder().state).toBe("recording");
    expect(capture.recorder().stream).toBe(first.stream);

    hand(capture, piece("fresh"));
    await waitFor(() => expect(api.calls(UPLOAD)).toHaveLength(2));

    const [before, after] = uploads(api);
    expect(after).toMatchObject({ seq: "0", body: "fresh" });
    expect(after?.part).not.toBe(before?.part);
    // The last piece of the run that was ended ("rest") belongs to the part before.
    expect(uploads(api).map((upload) => upload.body)).toEqual(["one", "fresh"]);
    expect(screen.getByRole("heading", { name: "You are live" })).toBeInTheDocument();
  });

  test("a recorder that hands nothing over is started again, as often as it takes, and the studio stays live", async () => {
    const { capture } = await goLiveAsAdmin({ broadcast: { restart_window_ms: 60 } });
    const first = capture.recorder();

    // Nothing arrives: one run of the recorder after the other, on the same camera.
    await waitFor(() => expect(capture.recorders.length).toBeGreaterThan(4), { timeout: 5000 });

    expect(first.state).toBe("inactive");
    expect(capture.recorder().state).toBe("recording");
    expect(capture.recorder().stream).toBe(first.stream);
    expect(screen.getByRole("heading", { name: "You are live" })).toBeInTheDocument();
  });

  test("a picture that changes its shape begins another part", async () => {
    const { capture } = await goLiveAsAdmin();
    const video = screen.getByLabelText("Preview") as HTMLVideoElement;
    const size = (width: number, height: number) => {
      Object.defineProperty(video, "videoWidth", { configurable: true, value: width });
      Object.defineProperty(video, "videoHeight", { configurable: true, value: height });
      act(() => void video.dispatchEvent(new Event("resize")));
    };

    size(1280, 720);
    expect(capture.recorders).toHaveLength(1);

    // The phone was turned.
    size(720, 1280);

    expect(capture.recorders).toHaveLength(2);
    expect(capture.recorder().state).toBe("recording");
  });
});

describe("the portal's sign-in ends while the studio is live", () => {
  test("the sign-in banner with a link into another tab; the page stays and the same piece goes up again", async () => {
    const reload = vi.spyOn(page, "reload").mockImplementation(() => {});
    let signedIn = false;
    const { api, capture } = await goLiveAsAdmin({
      routes: { [UPLOAD]: (request) => (signedIn ? taken(request) : portal401()) },
    });

    hand(capture, piece("one"));

    expect(
      await screen.findByText(
        "Your ETH sign-in has ended. Sign in again in another tab; the stream goes on from here.",
      ),
    ).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /^Sign in again/ });
    expect(link).toHaveAttribute("href", "/");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAccessibleName("Sign in again (opens in another tab)");
    // The studio goes on: its recorder runs, and the piece is tried again and again.
    await waitFor(() => expect(api.calls(UPLOAD).length).toBeGreaterThan(2));
    expect(capture.recorder().state).toBe("recording");
    expect(screen.getByRole("heading", { name: "You are live" })).toBeInTheDocument();
    expect(reload).not.toHaveBeenCalled();

    // The broadcaster signed in in the other tab: the piece is taken, and the banner goes.
    signedIn = true;
    await waitFor(() => expect(screen.queryByText(/Your ETH sign-in has ended/)).toBeNull());
    const sent = uploads(api);
    expect(new Set(sent.map((upload) => `${upload.part} ${upload.seq} ${upload.body}`)).size).toBe(
      1,
    );
    expect(sent[0]).toMatchObject({ seq: "0", body: "one" });
    expect(reload).not.toHaveBeenCalled();
  });

  test("while it is signed out the studio follows no topic, so that no poll asks the API and reloads the page", async () => {
    const streams = installFakeEventSource();
    let signedIn = false;
    const { capture } = await goLiveAsAdmin({
      live: true,
      routes: { [UPLOAD]: (request) => (signedIn ? taken(request) : portal401()) },
    });
    expect(streams.latest().topics).toEqual([`broadcast:${MARKET_ID}`]);

    hand(capture, piece("one"));
    await screen.findByText(/Your ETH sign-in has ended/);

    await waitFor(() => expect(streams.latest().topics).toEqual([]));

    signedIn = true;
    await waitFor(() => expect(streams.latest().topics).toEqual([`broadcast:${MARKET_ID}`]));
  });
});

describe("End stream", () => {
  test("opens a dialog that states the consequence; 'Cancel' closes it and nothing is sent", async () => {
    const { api, capture, user } = await goLiveAsAdmin({ routes: { [END]: json(200, {}) } });

    await user.click(screen.getByRole("button", { name: "End stream" }));

    const dialog = await screen.findByRole("alertdialog", { name: "End the live stream?" });
    expect(
      within(dialog).getByText("Viewers see that it has ended. No picture and no sound is kept."),
    ).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "End stream" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Close" })).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(api.calls(END)).toHaveLength(0);
    expect(capture.recorder().state).toBe("recording");
  });

  test("its 'End stream' ends the live stream: the ended state, the camera let go, nothing more sent", async () => {
    const ended = own("ENDED", {}, {});
    const { api, capture, user } = await goLiveAsAdmin({ routes: { [END]: json(200, ended) } });

    await user.click(screen.getByRole("button", { name: "End stream" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "End stream" }));

    expect(
      await screen.findByRole("heading", { name: "The live stream has ended." }),
    ).toBeInTheDocument();
    expect(api.lastCall(END)?.path).toBe(`/api/broadcasts/${BROADCAST_ID}/end`);
    expect(screen.getByText("No picture and no sound is kept.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to the market" })).toHaveAttribute(
      "href",
      `/markets/${MARKET_ID}`,
    );
    // The broadcaster ended it: no sentence of a reason, and nothing to start again.
    expect(screen.queryByRole("button", { name: "Go live again" })).not.toBeInTheDocument();
    expect(capture.recorder().state).toBe("inactive");
    expect(capture.stream().released).toBe(true);
    expect(preview()).not.toBeInTheDocument();
    await waitFor(() => expect(tabBar()).toBeInTheDocument());
    expect(api.calls(UPLOAD)).toHaveLength(0);
  });

  test("a refusal is shown in the dialog, and the stream goes on", async () => {
    const { capture, user } = await goLiveAsAdmin({
      routes: { [END]: apiError(409, "CONFLICT", "This live stream cannot be ended right now.") },
    });
    await user.click(screen.getByRole("button", { name: "End stream" }));
    const dialog = await screen.findByRole("alertdialog");

    await user.click(within(dialog).getByRole("button", { name: "End stream" }));

    expect(
      await within(dialog).findByText("This live stream cannot be ended right now."),
    ).toBeInTheDocument();
    expect(capture.recorder().state).toBe("recording");
    // The studio stands behind its dialog, live as before.
    expect(screen.getByText("You are live")).toBeInTheDocument();
  });
});

describe("the live stream ends without a tap here", () => {
  test("an admin ended it: the announcement reads the answer, and the studio shows the ended state with the reason", async () => {
    const streams = installFakeEventSource();
    const { api, capture } = await goLiveAsAdmin({ live: true });
    act(() => streams.latest().send(readyEvent()));
    await waitFor(() => expect(tabBar()).not.toBeInTheDocument());

    api.on(BROADCAST, json(200, own("ENDED", { end_reason: "ADMIN" })));
    act(() => streams.latest().send(broadcastChangedEvent()));

    expect(
      await screen.findByRole("heading", { name: "The live stream has ended." }),
    ).toBeInTheDocument();
    expect(screen.getByText("VIS ended it.")).toBeInTheDocument();
    expect(capture.recorder().state).toBe("inactive");
    expect(capture.stream().released).toBe(true);
    await waitFor(() => expect(tabBar()).toBeInTheDocument());
  });

  test("an announcement about another market reads nothing", async () => {
    const streams = installFakeEventSource();
    const { api } = await goLiveAsAdmin({ live: true });
    act(() => streams.latest().send(readyEvent()));
    await waitFor(() => expect(api.calls(BROADCAST).length).toBeGreaterThan(1));
    const before = api.calls(BROADCAST).length;

    act(() => streams.latest().send(broadcastChangedEvent({ market_id: NEXT_BROADCAST_ID })));
    await new Promise((resolve) => setTimeout(resolve, 30));

    expect(api.calls(BROADCAST)).toHaveLength(before);
  });

  test("the API refuses a piece: the upload stops for good, and the ended state shows the API's sentence and the reason", async () => {
    const { api, capture } = await goLiveAsAdmin({
      routes: { [UPLOAD]: apiError(409, "CONFLICT", "This live stream has ended.") },
    });
    api.on(BROADCAST, json(200, own("ENDED", { end_reason: "TIME_LIMIT" })));

    hand(capture, piece("one"));

    expect(
      await screen.findByRole("heading", { name: "The live stream has ended." }),
    ).toBeInTheDocument();
    expect(screen.getByText("This live stream has ended.")).toBeInTheDocument();
    expect(await screen.findByText("It reached the limit of 120 minutes.")).toBeInTheDocument();
    expect(capture.recorder().state).toBe("inactive");
    expect(capture.stream().released).toBe(true);
    expect(api.calls(UPLOAD)).toHaveLength(1);
  });

  test("it ended for lack of video and may go on: 'Go live again' starts it, and the pieces go to the stream of that answer", async () => {
    const { api, capture, user } = renderStudio({
      answer: own("APPROVED", {}, { can_start: true }),
      routes: {
        [START]: json(200, onAir()),
        [UPLOAD]: apiError(409, "CONFLICT", "This live stream has ended."),
      },
    });
    await tapGoLive(user);
    await screen.findByRole("heading", { name: "You are live" });
    api.on(BROADCAST, json(200, own("ENDED", { end_reason: "STALLED" }, { can_start: true })));
    hand(capture, piece("one"));
    expect(await screen.findByText("No video arrived for 120 seconds.")).toBeInTheDocument();
    expect(capture.stream().released).toBe(true);

    api.on(START, json(200, onAir({ id: NEXT_BROADCAST_ID })));
    api.on(UPLOAD, taken);
    await user.click(screen.getByRole("button", { name: "Go live again" }));

    expect(await screen.findByRole("heading", { name: "You are live" })).toBeInTheDocument();
    // The start names the stream that ended; the camera was asked for again.
    expect(api.lastCall(START)?.path).toBe(`/api/broadcasts/${BROADCAST_ID}/start`);
    expect(capture.requests).toHaveLength(2);
    hand(capture, piece("again"));
    await waitFor(() => expect(uploads(api).at(-1)).toMatchObject({ body: "again" }));
    expect(uploads(api).at(-1)).toMatchObject({ broadcast: NEXT_BROADCAST_ID, seq: "0" });
  });
});

describe("'Go live again' with a camera the browser refuses", () => {
  test("shows the browser's banner in the ended state, and sends no start", async () => {
    const { api, capture, user } = renderStudio({
      answer: own("APPROVED", {}, { can_start: true }),
      routes: {
        [START]: json(200, onAir()),
        [UPLOAD]: apiError(409, "CONFLICT", "This live stream has ended."),
      },
    });
    await tapGoLive(user);
    await screen.findByRole("heading", { name: "You are live" });
    api.on(BROADCAST, json(200, own("ENDED", { end_reason: "STALLED" }, { can_start: true })));
    hand(capture, piece("one"));
    const again = await screen.findByRole("button", { name: "Go live again" });

    capture.setAccess("refused");
    await user.click(again);

    expect(
      await screen.findByText(
        "Camera and microphone access was refused. Allow both in your browser settings.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "The live stream has ended." })).toBeInTheDocument();
    expect(api.calls(START)).toHaveLength(1);
  });
});

describe("paused", () => {
  test("a return to the studio while the stream is LIVE: 'Resume' and 'End stream', and no camera yet", async () => {
    const { capture } = renderStudio({ answer: onAir(), broadcast: { stall_seconds: 45 } });

    expect(
      await screen.findByRole("heading", { name: "Your live stream is paused." }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("It ends by itself 45 seconds after the last picture arrived."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Resume" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "End stream" })).toBeInTheDocument();
    expect(preview()).not.toBeInTheDocument();
    expect(capture.requests).toEqual([]);
    expect(tabBar()).toBeInTheDocument();
  });

  test("'Resume' asks for the camera again and goes on sending in another part, without another start", async () => {
    const { api, capture, user } = renderStudio({ answer: onAir() });

    await user.click(await screen.findByRole("button", { name: "Resume" }));

    expect(await screen.findByRole("heading", { name: "You are live" })).toBeInTheDocument();
    expect(capture.requests).toHaveLength(1);
    hand(capture, piece("one"));
    await waitFor(() => expect(api.calls(UPLOAD)).toHaveLength(1));
    expect(uploads(api)).toMatchObject([{ broadcast: BROADCAST_ID, seq: "0", body: "one" }]);
    expect(api.calls(START)).toHaveLength(0);
    expect(api.calls(START_AS_ADMIN)).toHaveLength(0);
  });

  test("'Resume' with a camera the browser refuses: its banner, and the studio stays paused", async () => {
    const { user } = renderStudio({ answer: onAir(), capture: { access: "refused" } });

    await user.click(await screen.findByRole("button", { name: "Resume" }));

    expect(
      await screen.findByText(
        "Camera and microphone access was refused. Allow both in your browser settings.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Your live stream is paused." }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Resume" })).toBeEnabled();
  });

  test.each([
    ["the camera goes away", (capture: FakeCapture) => capture.stream().video.end()],
    ["the microphone goes away", (capture: FakeCapture) => capture.stream().audio.end()],
    ["the recorder gives up", (capture: FakeCapture) => capture.recorder().fail()],
  ])("%s: the studio pauses, lets the camera go and sends nothing more", async (_name, happen) => {
    const { api, capture } = await goLiveAsAdmin();
    const recorder = capture.recorder();

    act(() => happen(capture));

    expect(
      await screen.findByRole("heading", { name: "Your live stream is paused." }),
    ).toBeInTheDocument();
    expect(recorder.state).toBe("inactive");
    expect(capture.stream().released).toBe(true);
    await waitFor(() => expect(tabBar()).toBeInTheDocument());
    expect(api.calls(UPLOAD)).toHaveLength(0);
  });

  test("the upload has to start over three times within the window: the studio pauses", async () => {
    const { capture } = await goLiveAsAdmin({
      routes: { [UPLOAD]: json(200, broadcastChunk({ restart: true, next_seq: 0 })) },
    });

    hand(capture, piece("one"));
    await waitFor(() => expect(capture.recorders).toHaveLength(2));
    hand(capture, piece("two"));
    await waitFor(() => expect(capture.recorders).toHaveLength(3));
    hand(capture, piece("three"));

    expect(
      await screen.findByRole("heading", { name: "Your live stream is paused." }),
    ).toBeInTheDocument();
    // No fourth run of the recorder.
    expect(capture.recorders).toHaveLength(3);
    expect(capture.recorder().state).toBe("inactive");
  });

  test("'End stream' ends a paused live stream as well", async () => {
    const { api, user } = renderStudio({
      answer: onAir(),
      routes: { [END]: json(200, own("ENDED")) },
    });

    await user.click(await screen.findByRole("button", { name: "End stream" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "End stream" }));

    expect(
      await screen.findByRole("heading", { name: "The live stream has ended." }),
    ).toBeInTheDocument();
    expect(api.calls(END)).toHaveLength(1);
  });
});

describe("the proposer of the market", () => {
  test("may ask: the request states policy_accepted once its box is ticked, and the studio then waits for VIS", async () => {
    const { api, capture, user } = renderStudio({
      answer: noBroadcast({ can_request: true }),
      routes: { [REQUEST]: json(200, own("REQUESTED")) },
    });

    const button = await screen.findByRole("button", { name: "Request a live stream" });
    expect(
      screen.getByText(
        "Ask VIS to let you stream this market live, from your camera. An admin decides on every request.",
      ),
    ).toBeInTheDocument();
    expect(button).toBeDisabled();

    await user.click(screen.getByRole("checkbox", { name: /I accept the usage policy/ }));
    await user.click(button);

    expect(
      await screen.findByText("You asked for a live stream. VIS decides on it."),
    ).toBeInTheDocument();
    expect(api.lastCall(REQUEST)?.body).toEqual({ policy_accepted: true });
    expect(await screen.findByText("Request sent. VIS decides on it.")).toBeInTheDocument();
    expect(screen.getByText("In review")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Request a live stream" })).toBeNull();
    expect(capture.requests).toEqual([]);
  });

  test("a refusal of the request stands above its button", async () => {
    const { user } = renderStudio({
      answer: noBroadcast({ can_request: true }),
      routes: {
        [REQUEST]: apiError(409, "LIMIT_EXCEEDED", "You asked as often as a day allows."),
      },
    });

    await user.click(await screen.findByRole("checkbox", { name: /I accept the usage policy/ }));
    await user.click(screen.getByRole("button", { name: "Request a live stream" }));

    expect(await screen.findByText("You asked as often as a day allows.")).toBeInTheDocument();
  });

  test("waits: 'In review', the way back to the market, and no camera", async () => {
    const { capture } = renderStudio({ answer: own("REQUESTED") });

    expect(
      await screen.findByText("You asked for a live stream. VIS decides on it."),
    ).toBeInTheDocument();
    expect(screen.getByText("In review")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to the market" })).toHaveAttribute(
      "href",
      `/markets/${MARKET_ID}`,
    );
    expect(screen.queryByRole("button", { name: "Go live" })).toBeNull();
    expect(capture.requests).toEqual([]);
  });

  test("was refused: the admin's reason, and the request again while the API lets them ask", async () => {
    renderStudio({ answer: own("REFUSED", {}, { can_request: true }) });

    expect(await screen.findByText("Your request was not approved.")).toBeInTheDocument();
    expect(screen.getByText("The market closes before the event starts.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Request a live stream" })).toBeDisabled();
  });

  test("is approved while the page waits: 'Set up the live stream', and the camera only with that tap", async () => {
    const streams = installFakeEventSource();
    const { api, capture, user } = renderStudio({ answer: own("REQUESTED"), live: true });
    await screen.findByText("You asked for a live stream. VIS decides on it.");
    act(() => streams.latest().send(readyEvent()));

    api.on(BROADCAST, json(200, own("APPROVED", {}, { can_start: true })));
    act(() => streams.latest().send(broadcastChangedEvent()));

    expect(
      await screen.findByText("Your live stream is approved. Start it when you are ready."),
    ).toBeInTheDocument();
    expect(capture.requests).toEqual([]);

    await user.click(screen.getByRole("button", { name: "Set up the live stream" }));

    expect(await screen.findByLabelText("Preview")).toBeInTheDocument();
    expect(capture.requests).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Go live" })).toBeInTheDocument();
  });

  test("sets up while VIS refuses after all: the reason in place of the set-up, and the camera is let go", async () => {
    const streams = installFakeEventSource();
    const { api, capture } = renderStudio({
      answer: own("APPROVED", {}, { can_start: true }),
      live: true,
    });
    await screen.findByLabelText("Preview");
    act(() => streams.latest().send(readyEvent()));

    api.on(BROADCAST, json(200, own("REFUSED", {}, { can_request: true })));
    act(() => streams.latest().send(broadcastChangedEvent()));

    expect(await screen.findByText("Your request was not approved.")).toBeInTheDocument();
    expect(screen.getByText("The market closes before the event starts.")).toBeInTheDocument();
    expect(preview()).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Go live" })).toBeNull();
    // The screen stays; its camera does not.
    expect(capture.stream().released).toBe(true);
  });

  test("whose live stream ended and may not go on: the sentence of its end, and no start", async () => {
    renderStudio({ answer: own("ENDED", { end_reason: "ADMIN" }) });

    expect(
      await screen.findByText("Your live stream has ended. VIS ended it."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Go live/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Set up the live stream" })).toBeNull();
  });

  test("whose live stream ended by itself opens the set-up from the market, and starts the stream that ended", async () => {
    const { api, user } = renderStudio({
      answer: own("ENDED", { end_reason: "STALLED" }, { can_start: true }),
      routes: { [START]: json(200, onAir({ id: NEXT_BROADCAST_ID })) },
    });

    await tapGoLive(user);

    expect(await screen.findByRole("heading", { name: "You are live" })).toBeInTheDocument();
    expect(api.lastCall(START)?.path).toBe(`/api/broadcasts/${BROADCAST_ID}/start`);
  });
});

describe("what the studio follows", () => {
  test("it names the live stream of its market to the app's stream, and every opening reads the answer again", async () => {
    const streams = installFakeEventSource();
    const { api } = renderStudio({ answer: adminMayStart(), live: true });
    await screen.findByLabelText("Preview");

    await waitFor(() => expect(streams.latest().topics).toEqual([`broadcast:${MARKET_ID}`]));
    act(() => streams.latest().send(readyEvent()));

    await waitFor(() => expect(api.calls(BROADCAST)).toHaveLength(2));
  });
});
