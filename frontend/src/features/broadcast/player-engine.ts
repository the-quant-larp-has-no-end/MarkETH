import type { HlsConfig } from "hls.js";

/**
 * How a browser plays a live stream (DESIGN.md §12, "Video box"; §14, "Live video"). A
 * live stream is a playlist on the app's own origin with its segments, and a browser
 * plays one in one of two ways:
 *
 * | Case | The browser                                             | Plays through             |
 * | ---- | ------------------------------------------------------- | ------------------------- |
 * | 1    | Apple's engine, and it claims the type of a playlist    | its own player            |
 * | 2    | any other with Media Source Extensions that hls.js takes | hls.js                    |
 * | 3    | neither, but it claims the type of a playlist           | its own player            |
 * | 4    | none of the three                                       | nothing: the box says so  |
 *
 * Apple's engine is Safari and every browser on an iPhone or an iPad. It never gets
 * hls.js: where a recorder begins again the playlist has a break, hls.js stops at it
 * there and the engine's own player plays across it. Every other browser gets hls.js,
 * also where it claims the type: its Media Source Extensions are what is proven.
 *
 * hls.js is a chunk of the app's own origin. It is loaded by the first "Watch live" of a
 * browser of case 2 and by nothing else, and it runs without a worker: the
 * Content-Security-Policy allows no remote script and no worker.
 */

/** The type a playlist is served as: what a browser is asked about. */
export const HLS_TYPE = "application/vnd.apple.mpegurl";

/**
 * How hls.js plays a live stream. The three figures count segments of the playlist: play
 * three behind its end; a viewer who fell more than six behind (a stall, a starved
 * connection) is taken back to three; keep ten seconds of what was played. No worker.
 */
export const HLS_SETTINGS = {
  enableWorker: false,
  liveSyncDurationCount: 3,
  liveMaxLatencyDurationCount: 6,
  backBufferLength: 10,
} as const satisfies Partial<HlsConfig>;

/** Which player a browser gets: its own, hls.js, or none. */
export type PlayerEngine = "own" | "hls" | "none";

/**
 * The engine of this browser, read without loading anything. "hls" is the answer for
 * every browser with Media Source Extensions outside Apple's engine; whether hls.js takes
 * them (a build without H.264 has them too) is known once it is loaded, and
 * `playLiveStream` falls back to case 3 or 4 then.
 */
export function chooseEngine(video: HTMLVideoElement): PlayerEngine {
  const claimsPlaylist = video.canPlayType(HLS_TYPE) !== "";
  if (claimsPlaylist && (navigator.vendor ?? "").startsWith("Apple")) return "own";
  if ("MediaSource" in window || "ManagedMediaSource" in window) return "hls";
  return claimsPlaylist ? "own" : "none";
}

/** What a player tells whoever built it. Each is told at most once per player. */
export type PlayerEvents = {
  /** The browser can play a live stream in no way (case 4). */
  onUnplayable: () => void;
  /**
   * The player gave up: a fatal error of hls.js, an error of the element (a playlist that
   * is refused or gone is one), or hls.js itself could not be loaded.
   */
  onFailed: () => void;
  /**
   * The browser holds the picture back until its viewer starts it: the box's own play
   * control does that. Nothing failed.
   */
  onHeld: () => void;
};

/**
 * Plays the playlist at `playlistUrl` in `video` until the returned function is called,
 * which takes the player away and makes the browser stop asking for video. It is called
 * for a tap ("Watch live"), never by a page that loads.
 *
 * A player recovers nothing by itself: what fails is told once through `onFailed`, and
 * whoever built the player asks the API what became of the live stream and builds another.
 */
export function playLiveStream(
  video: HTMLVideoElement,
  playlistUrl: string,
  events: PlayerEvents,
): () => void {
  let stopped = false;
  let told = false;
  /** Takes the source away from the element. */
  let release: (() => void) | null = null;

  /** Tells one thing, once, and nothing after the player was stopped. */
  const tell = (event: () => void) => {
    if (stopped || told) return;
    told = true;
    event();
  };
  const fail = () => tell(events.onFailed);

  const start = () => {
    // A browser that plays nothing by itself refuses with NotAllowedError; every other
    // refusal is an error of the element, or a player that was taken away meanwhile.
    void video.play()?.catch((error: unknown) => {
      if (stopped) return;
      if (error instanceof DOMException && error.name === "NotAllowedError") events.onHeld();
    });
  };

  const playItself = () => {
    video.src = playlistUrl;
    release = () => {
      video.removeAttribute("src");
      video.load();
    };
    start();
  };

  video.addEventListener("error", fail);

  const engine = chooseEngine(video);
  if (engine === "own") {
    playItself();
  } else if (engine === "none") {
    // Told a moment later, like everything else a player tells: never while it is built.
    queueMicrotask(() => tell(events.onUnplayable));
  } else {
    void import("hls.js").then(({ default: Hls }) => {
      if (stopped) return;
      if (!Hls.isSupported()) {
        if (video.canPlayType(HLS_TYPE) === "") tell(events.onUnplayable);
        else playItself();
        return;
      }
      const hls = new Hls(HLS_SETTINGS);
      release = () => hls.destroy();
      hls.on(Hls.Events.ERROR, (_name, data) => {
        if (data.fatal) fail();
      });
      hls.on(Hls.Events.MANIFEST_PARSED, start);
      hls.loadSource(playlistUrl);
      hls.attachMedia(video);
    }, fail);
  }

  return () => {
    if (stopped) return;
    stopped = true;
    video.removeEventListener("error", fail);
    // An element that was given nothing has nothing to stop.
    if (release === null) return;
    video.pause();
    release();
  };
}
