import { beforeEach, describe, expect, test, vi } from "vitest";

import { BROADCAST_ID } from "@/test/fixtures";

import { FakeHls, type FakeBrowser, installBrowser, resetHls, SAFARI } from "./testing";

const PLAYLIST = `/api/broadcasts/${BROADCAST_ID}/hls/index.m3u8`;

/** How often hls.js was loaded in this test. */
let loads = 0;

beforeEach(() => {
  // Every test loads the engine afresh, so that its `import("hls.js")` is a first one.
  vi.resetModules();
  loads = 0;
  resetHls();
  vi.doMock("hls.js", () => {
    loads += 1;
    return { default: FakeHls };
  });
});

const engine = () => import("./player-engine");

function listeners() {
  return { onUnplayable: vi.fn(), onFailed: vi.fn(), onHeld: vi.fn() };
}

/** A player in the browser the test names, with what it tells. */
async function build(browser: FakeBrowser = {}) {
  const element = installBrowser(browser);
  const { playLiveStream } = await engine();
  const video = document.createElement("video");
  const events = listeners();
  const stop = playLiveStream(video, PLAYLIST, events);
  return { video, events, stop, element };
}

/** hls.js arrives a moment after it was asked for, as a chunk does. */
const loaded = () => vi.waitFor(() => expect(loads).toBe(1));
const settled = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("which player a browser gets", () => {
  async function chosen(browser: FakeBrowser) {
    installBrowser(browser);
    const { chooseEngine } = await engine();
    return chooseEngine(document.createElement("video"));
  }

  test("case 1: Apple's engine plays a playlist itself", async () => {
    expect(await chosen(SAFARI)).toBe("own");
  });

  test("case 1 holds for a Safari that has Media Source Extensions too: never hls.js there", async () => {
    expect(await chosen({ ...SAFARI, mediaSource: true })).toBe("own");
  });

  test("case 2: every other browser with Media Source Extensions gets hls.js", async () => {
    expect(await chosen({ vendor: "Google Inc.", claimsPlaylist: false })).toBe("hls");
    expect(await chosen({ vendor: "", claimsPlaylist: false })).toBe("hls");
  });

  test("case 2 holds for a Chrome that claims the type of a playlist", async () => {
    expect(await chosen({ vendor: "Google Inc.", claimsPlaylist: true })).toBe("hls");
  });

  test("case 3: no extensions, but the browser claims the type: its own player", async () => {
    expect(await chosen({ vendor: "", claimsPlaylist: true, mediaSource: false })).toBe("own");
  });

  test("case 4: none of the three", async () => {
    expect(await chosen({ vendor: "", claimsPlaylist: false, mediaSource: false })).toBe("none");
  });
});

describe("the browser's own player", () => {
  test("it is given the playlist and started, and hls.js is not loaded", async () => {
    const { video, events, element } = await build(SAFARI);

    expect(video.getAttribute("src")).toBe(PLAYLIST);
    expect(element.play).toHaveBeenCalledTimes(1);
    await settled();
    expect(loads).toBe(0);
    expect(FakeHls.instances).toEqual([]);
    expect(events.onFailed).not.toHaveBeenCalled();
    expect(events.onHeld).not.toHaveBeenCalled();
  });

  test("an error of the element is told once", async () => {
    const { video, events } = await build(SAFARI);

    video.dispatchEvent(new Event("error"));
    video.dispatchEvent(new Event("error"));

    expect(events.onFailed).toHaveBeenCalledTimes(1);
  });

  test("a browser that starts no picture by itself is told as held, not as failed", async () => {
    const { events } = await build({ ...SAFARI, play: "refused" });

    await vi.waitFor(() => expect(events.onHeld).toHaveBeenCalledTimes(1));
    expect(events.onFailed).not.toHaveBeenCalled();
  });

  test("stopping takes the playlist away, so the browser asks for no more video", async () => {
    const { video, events, stop, element } = await build(SAFARI);

    stop();

    expect(video.hasAttribute("src")).toBe(false);
    expect(element.pause).toHaveBeenCalledTimes(1);
    expect(element.load).toHaveBeenCalledTimes(1);
    // What the element says afterwards belongs to nobody.
    video.dispatchEvent(new Event("error"));
    expect(events.onFailed).not.toHaveBeenCalled();
  });
});

describe("hls.js", () => {
  test("it is loaded for a browser of case 2, with the settings that were measured", async () => {
    const { video } = await build();
    await loaded();

    const hls = FakeHls.latest();
    expect(hls.config).toEqual({
      enableWorker: false,
      liveSyncDurationCount: 3,
      liveMaxLatencyDurationCount: 6,
      backBufferLength: 10,
    });
    expect(hls.source).toBe(PLAYLIST);
    expect(hls.media).toBe(video);
    // hls.js feeds the element itself: the playlist is not its source.
    expect(video.hasAttribute("src")).toBe(false);
  });

  test("the picture starts once the playlist is read", async () => {
    const { element } = await build();
    await loaded();
    expect(element.play).not.toHaveBeenCalled();

    FakeHls.latest().parsed();

    expect(element.play).toHaveBeenCalledTimes(1);
  });

  test("a fatal error is told once; one hls.js recovers from is not told", async () => {
    const { events } = await build();
    await loaded();
    const hls = FakeHls.latest();

    hls.fail(false);
    expect(events.onFailed).not.toHaveBeenCalled();

    hls.fail();
    hls.fail();
    expect(events.onFailed).toHaveBeenCalledTimes(1);
  });

  test("a browser that starts no picture by itself is told as held", async () => {
    const { events } = await build({ play: "refused" });
    await loaded();

    FakeHls.latest().parsed();

    await vi.waitFor(() => expect(events.onHeld).toHaveBeenCalledTimes(1));
    expect(events.onFailed).not.toHaveBeenCalled();
  });

  test("stopping destroys the player", async () => {
    const { stop } = await build();
    await loaded();
    const hls = FakeHls.latest();

    stop();

    expect(hls.destroyed).toBe(true);
  });

  test("a player that is stopped before hls.js arrived builds nothing", async () => {
    const { events, stop, element } = await build();

    stop();
    await loaded();
    await settled();

    expect(FakeHls.instances).toEqual([]);
    expect(events.onFailed).not.toHaveBeenCalled();
    // The element was given nothing: there is nothing to stop.
    expect(element.pause).not.toHaveBeenCalled();
    expect(element.load).not.toHaveBeenCalled();
  });

  test("hls.js that cannot be loaded is a player that failed", async () => {
    vi.doMock("hls.js", () => {
      throw new Error("The chunk could not be loaded.");
    });
    const { events } = await build();

    await vi.waitFor(() => expect(events.onFailed).toHaveBeenCalledTimes(1));
    expect(events.onUnplayable).not.toHaveBeenCalled();
  });
});

describe("a browser whose extensions hls.js cannot use", () => {
  test("case 3: it claims the type of a playlist, so its own player gets it", async () => {
    FakeHls.supported = false;
    const { video, events, element } = await build({ claimsPlaylist: true });
    await loaded();

    await vi.waitFor(() => expect(video.getAttribute("src")).toBe(PLAYLIST));
    expect(FakeHls.instances).toEqual([]);
    expect(element.play).toHaveBeenCalledTimes(1);
    expect(events.onUnplayable).not.toHaveBeenCalled();
  });

  test("case 4: it claims nothing, so the live stream cannot be played", async () => {
    FakeHls.supported = false;
    const { video, events } = await build({ claimsPlaylist: false });
    await loaded();

    await vi.waitFor(() => expect(events.onUnplayable).toHaveBeenCalledTimes(1));
    expect(FakeHls.instances).toEqual([]);
    expect(video.hasAttribute("src")).toBe(false);
  });
});

describe("a browser that can play a live stream in no way", () => {
  test("case 4 is told, and nothing is loaded or asked for", async () => {
    const { video, events, element } = await build({
      vendor: "",
      claimsPlaylist: false,
      mediaSource: false,
    });

    await vi.waitFor(() => expect(events.onUnplayable).toHaveBeenCalledTimes(1));
    await settled();
    expect(loads).toBe(0);
    expect(video.hasAttribute("src")).toBe(false);
    expect(element.play).not.toHaveBeenCalled();
    expect(events.onFailed).not.toHaveBeenCalled();
  });

  test("a player that is stopped at once tells nothing", async () => {
    installBrowser({ vendor: "", claimsPlaylist: false, mediaSource: false });
    const { playLiveStream } = await engine();
    const events = listeners();

    // In one step, as an effect that is cleaned up at once does.
    playLiveStream(document.createElement("video"), PLAYLIST, events)();
    await settled();

    expect(events.onUnplayable).not.toHaveBeenCalled();
  });
});
