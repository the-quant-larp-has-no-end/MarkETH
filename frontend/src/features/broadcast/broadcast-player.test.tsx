import { act, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  BROADCAST_ID,
  broadcastIn,
  broadcastItem,
  broadcastViewer,
  MARKET_ID,
  marketBroadcast,
} from "@/test/fixtures";
import { apiError, json, mockApi, noAnswer, type Route } from "@/test/http";
import { renderWithProviders } from "@/test/render";

import { BroadcastPlayer } from "./broadcast-player";
import {
  type FakeBrowser,
  FakeHls,
  installBrowser,
  resetHls,
  SAFARI,
  showFrame,
  tell,
} from "./testing";

vi.mock("hls.js", async () => (await import("./testing")).hlsModule);

const BROADCAST: Route = "GET /api/markets/{market_id}/broadcast";
const PLAYLIST = `/api/broadcasts/${BROADCAST_ID}/hls/index.m3u8`;
const QUESTION = "Will the Polybahn run without interruption during the exam session?";

/** The paces of `config()`: `wait_notice_ms` and `player_retry_ms` of `.example.env`. */
const WAIT_NOTICE_MS = 4000;
const PLAYER_RETRY_MS = 2000;
/**
 * The clock of a test also moves with the time the test takes, so "not yet" is looked at
 * this long before a pace has passed.
 */
const EARLY_MS = 1000;

beforeEach(() => {
  resetHls();
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
});

function renderPlayer(browser: FakeBrowser = {}, answer: Response = json(200, marketBroadcast())) {
  const element = installBrowser(browser);
  const api = mockApi({ [BROADCAST]: answer });
  const view = renderWithProviders(
    <BroadcastPlayer marketId={MARKET_ID} playlistUrl={PLAYLIST} question={QUESTION} />,
  );
  return { ...view, api, element };
}

const box = () => document.querySelector<HTMLElement>("[data-slot=video-box]")!;
const video = () => screen.getByLabelText<HTMLVideoElement>(`Live stream: ${QUESTION}`);
const pass = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms));

/** The players hls.js built so far. */
const players = async (count: number) => {
  await waitFor(() => expect(FakeHls.instances).toHaveLength(count));
  return FakeHls.latest();
};

describe("the box", () => {
  test("it is the browser's own video element, with its controls and a name", async () => {
    renderPlayer();
    await players(1);

    expect(video().tagName).toBe("VIDEO");
    expect(video()).toHaveAttribute("controls");
    expect(video()).toHaveAttribute("playsinline");
    expect(video()).toHaveAttribute("preload", "none");
    expect(video()).toHaveClass("object-contain");
    expect(box()).toHaveClass("aspect-video", "rounded-md", "bg-background");
  });

  test("without a question it is named a live stream", async () => {
    installBrowser();
    mockApi();
    renderWithProviders(<BroadcastPlayer marketId={MARKET_ID} playlistUrl={PLAYLIST} />);

    expect(screen.getByLabelText("Live stream").tagName).toBe("VIDEO");
    await players(1);
  });

  test("until the first frame it shows a skeleton and says that it connects", async () => {
    renderPlayer();
    await players(1);

    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("Connecting to the live stream");
    expect(status.querySelector("[data-slot=skeleton]")).not.toBeNull();
    expect(screen.getByText("Connecting to the live stream")).toHaveClass("sr-only");
  });

  test("the first frame takes the skeleton away", async () => {
    renderPlayer();
    await players(1);

    showFrame(video(), 0.4);

    expect(screen.queryByText("Connecting to the live stream")).not.toBeInTheDocument();
    expect(box().querySelector("[data-slot=skeleton]")).toBeNull();
    expect(box()).toHaveAttribute("data-phase", "playing");
  });

  test("the player is given the playlist of the live stream", async () => {
    renderPlayer();

    expect((await players(1)).source).toBe(PLAYLIST);
    expect(FakeHls.latest().media).toBe(video());
  });

  test("leaving takes the player away", async () => {
    const { unmount } = renderPlayer();
    const hls = await players(1);

    unmount();

    expect(hls.destroyed).toBe(true);
  });

  test("Apple's engine is given the playlist itself, and leaving takes it away", async () => {
    const { unmount, element } = renderPlayer(SAFARI);

    const shown = video();
    expect(shown.getAttribute("src")).toBe(PLAYLIST);
    expect(element.play).toHaveBeenCalledTimes(1);

    unmount();

    expect(shown.hasAttribute("src")).toBe(false);
    expect(FakeHls.instances).toEqual([]);
  });
});

describe("no picture arrives", () => {
  test("after wait_notice_ms the box says that it waits, and the API is asked once", async () => {
    const { api } = renderPlayer();
    await players(1);

    await pass(WAIT_NOTICE_MS - EARLY_MS);
    expect(screen.queryByText("Waiting for the live stream…")).not.toBeInTheDocument();
    expect(api.calls(BROADCAST)).toHaveLength(0);

    await pass(EARLY_MS);

    expect(screen.getByRole("status")).toHaveTextContent("Waiting for the live stream…");
    expect(screen.getByText("Waiting for the live stream…")).toHaveClass("type-body-sm");
    await waitFor(() => expect(api.calls(BROADCAST)).toHaveLength(1));
  });

  test("the waiting line stands over the last frame: the element stays", async () => {
    renderPlayer();
    await players(1);
    showFrame(video(), 3);
    const shown = video();

    await pass(WAIT_NOTICE_MS);

    expect(screen.getByText("Waiting for the live stream…")).toBeInTheDocument();
    expect(video()).toBe(shown);
  });

  test("while the answer is this live stream, another player is built after player_retry_ms", async () => {
    const { api } = renderPlayer();
    const first = await players(1);

    await pass(WAIT_NOTICE_MS);
    await waitFor(() => expect(api.calls(BROADCAST)).toHaveLength(1));
    await pass(PLAYER_RETRY_MS - EARLY_MS);
    expect(FakeHls.instances).toHaveLength(1);

    await pass(EARLY_MS);

    const second = await players(2);
    expect(first.destroyed).toBe(true);
    expect(second.source).toBe(PLAYLIST);
    // The line stays until a picture is there.
    expect(screen.getByText("Waiting for the live stream…")).toBeInTheDocument();
  });

  test("as often as it takes, and a picture ends the waiting", async () => {
    const { api } = renderPlayer();
    await players(1);

    await pass(WAIT_NOTICE_MS);
    await waitFor(() => expect(api.calls(BROADCAST)).toHaveLength(1));
    await pass(PLAYER_RETRY_MS);
    await players(2);

    await pass(WAIT_NOTICE_MS);
    await waitFor(() => expect(api.calls(BROADCAST)).toHaveLength(2));
    await pass(PLAYER_RETRY_MS);
    await players(3);

    showFrame(video(), 0.2);

    expect(screen.queryByText("Waiting for the live stream…")).not.toBeInTheDocument();
    expect(box()).toHaveAttribute("data-phase", "playing");
  });

  test("a picture that moves is waited for from its last frame on", async () => {
    const { api } = renderPlayer();
    await players(1);

    await pass(WAIT_NOTICE_MS - 500);
    showFrame(video(), 1);
    await pass(WAIT_NOTICE_MS - 500);
    showFrame(video(), 2);
    await pass(WAIT_NOTICE_MS - 500);

    expect(screen.queryByText("Waiting for the live stream…")).not.toBeInTheDocument();
    expect(api.calls(BROADCAST)).toHaveLength(0);
  });

  test("an event of the element without another position is no picture", async () => {
    renderPlayer();
    await players(1);
    showFrame(video(), 1);

    await pass(WAIT_NOTICE_MS - 500);
    showFrame(video(), 1);
    await pass(500);

    expect(screen.getByText("Waiting for the live stream…")).toBeInTheDocument();
  });

  test("a picture that comes back by itself is kept: no player is built over it", async () => {
    const { api } = renderPlayer();
    await players(1);
    showFrame(video(), 1);

    await pass(WAIT_NOTICE_MS);
    await waitFor(() => expect(api.calls(BROADCAST)).toHaveLength(1));
    showFrame(video(), 1.5);
    // Longer than a player waits to be built, shorter than a picture is waited for.
    await pass(PLAYER_RETRY_MS + EARLY_MS);

    expect(FakeHls.instances).toHaveLength(1);
    expect(FakeHls.latest().destroyed).toBe(false);
    expect(screen.queryByText("Waiting for the live stream…")).not.toBeInTheDocument();
  });

  test("a viewer who pauses the picture is not waited for", async () => {
    const { api } = renderPlayer();
    await players(1);
    showFrame(video(), 1);

    tell(video(), "pause");
    await pass(WAIT_NOTICE_MS * 3);

    expect(screen.queryByText("Waiting for the live stream…")).not.toBeInTheDocument();
    expect(api.calls(BROADCAST)).toHaveLength(0);

    // Started again, the picture is waited for as before.
    tell(video(), "play");
    await pass(WAIT_NOTICE_MS);

    expect(screen.getByText("Waiting for the live stream…")).toBeInTheDocument();
  });

  test("a browser that holds the picture back shows its own controls and is not waited for", async () => {
    const { api } = renderPlayer({ ...SAFARI, play: "refused" });

    await waitFor(() => expect(box()).toHaveAttribute("data-phase", "held"));
    expect(screen.queryByText("Connecting to the live stream")).not.toBeInTheDocument();
    expect(video()).toHaveAttribute("controls");

    await pass(WAIT_NOTICE_MS * 3);

    expect(api.calls(BROADCAST)).toHaveLength(0);
    expect(box()).toHaveAttribute("data-phase", "held");
  });
});

describe("the player fails", () => {
  test("a fatal error asks the API at once, and the box says that it waits", async () => {
    const { api } = renderPlayer();
    const hls = await players(1);

    act(() => hls.fail());

    expect(screen.getByText("Waiting for the live stream…")).toBeInTheDocument();
    await waitFor(() => expect(api.calls(BROADCAST)).toHaveLength(1));
  });

  test("an error hls.js recovers from asks nothing", async () => {
    const { api } = renderPlayer();
    const hls = await players(1);

    act(() => hls.fail(false));
    await pass(100);

    expect(api.calls(BROADCAST)).toHaveLength(0);
    expect(screen.queryByText("Waiting for the live stream…")).not.toBeInTheDocument();
  });

  test("while the answer is this live stream, another player is built after player_retry_ms", async () => {
    const { api } = renderPlayer();
    const first = await players(1);

    act(() => first.fail());
    await waitFor(() => expect(api.calls(BROADCAST)).toHaveLength(1));
    await pass(PLAYER_RETRY_MS);

    await players(2);
    expect(first.destroyed).toBe(true);
    // The player that failed is not asked about a second time while the next one connects.
    expect(api.calls(BROADCAST)).toHaveLength(1);
  });

  test("an error of the element asks the API too", async () => {
    const { api, element } = renderPlayer(SAFARI);
    const shown = video();

    tell(shown, "error");
    await waitFor(() => expect(api.calls(BROADCAST)).toHaveLength(1));
    await pass(PLAYER_RETRY_MS);

    // Apple's engine is given the playlist again.
    await waitFor(() => expect(element.play).toHaveBeenCalledTimes(2));
    expect(video().getAttribute("src")).toBe(PLAYLIST);
  });

  test.each([
    [
      "has ended",
      marketBroadcast({
        broadcast: broadcastIn("ENDED"),
        viewer: broadcastViewer({ can_watch: false }),
      }),
    ],
    ["is full", marketBroadcast({ viewer: broadcastViewer({ can_watch: false, full: true }) })],
    [
      "is gone for this viewer",
      marketBroadcast({ broadcast: null, viewer: broadcastViewer({ can_watch: false }) }),
    ],
    [
      "is another one",
      marketBroadcast({
        broadcast: broadcastItem({
          id: "0b2d4f6a-1c3e-4a5b-8d7f-9e1a3c5b7d9f",
          playlist_url: "/api/broadcasts/0b2d4f6a-1c3e-4a5b-8d7f-9e1a3c5b7d9f/hls/index.m3u8",
        }),
      }),
    ],
  ])("a live stream that %s builds no other player", async (_what, answer) => {
    const { api } = renderPlayer({}, json(200, answer));
    const hls = await players(1);

    act(() => hls.fail());
    await waitFor(() => expect(api.calls(BROADCAST)).toHaveLength(1));
    await pass(PLAYER_RETRY_MS * 3);

    // What the answer says is the section's to show: it takes the box away.
    expect(FakeHls.instances).toHaveLength(1);
    expect(api.calls(BROADCAST)).toHaveLength(1);
    expect(screen.queryByText("The live stream could not be played.")).not.toBeInTheDocument();
  });
});

describe("the API cannot say what became of the live stream", () => {
  test.each([
    ["no answer", noAnswer],
    ["an error of the server", () => apiError(500, "INTERNAL_ERROR", "Something went wrong.")],
  ])("%s: the banner with Try again, in place of the picture", async (_what, answer) => {
    installBrowser();
    const api = mockApi({ [BROADCAST]: answer });
    renderWithProviders(
      <BroadcastPlayer marketId={MARKET_ID} playlistUrl={PLAYLIST} question={QUESTION} />,
    );
    const hls = await players(1);

    act(() => hls.fail());

    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent("The live stream could not be played.");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
    expect(screen.queryByLabelText(`Live stream: ${QUESTION}`)).not.toBeInTheDocument();
    expect(hls.destroyed).toBe(true);

    // No player is built until the viewer asks again.
    await pass(PLAYER_RETRY_MS * 3);
    expect(FakeHls.instances).toHaveLength(1);
    expect(api.calls(BROADCAST)).toHaveLength(1);
  });

  test("Try again asks, and an answer that is this live stream builds a player at once", async () => {
    installBrowser();
    const api = mockApi({ [BROADCAST]: noAnswer });
    const { user } = renderWithProviders(
      <BroadcastPlayer marketId={MARKET_ID} playlistUrl={PLAYLIST} question={QUESTION} />,
    );
    const hls = await players(1);
    act(() => hls.fail());
    await screen.findByRole("alert");

    api.on(BROADCAST, json(200, marketBroadcast()));
    await user.click(screen.getByRole("button", { name: "Try again" }));

    await players(2);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(video()).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Connecting to the live stream");
  });

  test("Try again that gets no answer leaves the banner", async () => {
    installBrowser();
    const api = mockApi({ [BROADCAST]: noAnswer });
    const { user } = renderWithProviders(
      <BroadcastPlayer marketId={MARKET_ID} playlistUrl={PLAYLIST} question={QUESTION} />,
    );
    const hls = await players(1);
    act(() => hls.fail());
    await screen.findByRole("alert");

    await user.click(screen.getByRole("button", { name: "Try again" }));

    await waitFor(() => expect(api.calls(BROADCAST)).toHaveLength(2));
    expect(screen.getByRole("alert")).toHaveTextContent("The live stream could not be played.");
    expect(FakeHls.instances).toHaveLength(1);
  });
});

describe("a browser that can play a live stream in no way", () => {
  test("the box says so, asks nobody and loads nothing", async () => {
    const { api } = renderPlayer({ vendor: "", claimsPlaylist: false, mediaSource: false });

    expect(await screen.findByText("This browser cannot play the live stream.")).toHaveClass(
      "type-body-sm",
    );
    expect(screen.queryByLabelText(`Live stream: ${QUESTION}`)).not.toBeInTheDocument();

    await pass(WAIT_NOTICE_MS * 2);

    expect(api.calls(BROADCAST)).toHaveLength(0);
    expect(FakeHls.instances).toEqual([]);
    expect(screen.queryByText("Waiting for the live stream…")).not.toBeInTheDocument();
  });

  test("a browser whose extensions hls.js cannot use, and that claims nothing, is such a browser", async () => {
    FakeHls.supported = false;
    renderPlayer({ claimsPlaylist: false });

    expect(
      await screen.findByText("This browser cannot play the live stream."),
    ).toBeInTheDocument();
    expect(FakeHls.instances).toEqual([]);
  });
});

describe("leaving", () => {
  test("a player that waits to be built is not built", async () => {
    const { api, unmount } = renderPlayer();
    const hls = await players(1);
    act(() => hls.fail());
    await waitFor(() => expect(api.calls(BROADCAST)).toHaveLength(1));
    await pass(PLAYER_RETRY_MS / 2);

    unmount();
    await pass(PLAYER_RETRY_MS * 2);

    expect(FakeHls.instances).toHaveLength(1);
  });

  test("nothing is asked after the box is gone", async () => {
    const { api, unmount } = renderPlayer();
    await players(1);

    unmount();
    await pass(WAIT_NOTICE_MS * 2);

    expect(api.calls(BROADCAST)).toHaveLength(0);
  });
});
