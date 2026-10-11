import { act } from "@testing-library/react";
import { vi } from "vitest";

import type { ChatPanelProps } from "@/features/chat";

/**
 * What the tests of this feature share. It is imported by tests only.
 *
 * jsdom plays nothing: its `<video>` claims no type, has no Media Source Extensions and
 * says "not implemented" to `play()`. A test says which browser it is (`installBrowser`),
 * puts `FakeHls` in the place of hls.js, and moves the picture itself (`showFrame`).
 */

/** The names of the two events of hls.js the player hears. */
const EVENTS = { ERROR: "hlsError", MANIFEST_PARSED: "hlsManifestParsed" } as const;

/**
 * hls.js as the player uses it, driven by the test:
 *
 *     vi.mock("hls.js", async () => (await import("./testing")).hlsModule);
 *     ...
 *     const hls = FakeHls.latest();
 *     hls.parsed();          // the playlist arrived: the player starts the picture
 *     hls.fail();            // a fatal error
 *     hls.fail(false);       // an error hls.js recovers from by itself
 */
export class FakeHls {
  static readonly Events = EVENTS;
  static instances: FakeHls[] = [];
  /** What `Hls.isSupported()` answers: false is a browser whose extensions hls.js cannot use. */
  static supported = true;

  static isSupported(): boolean {
    return FakeHls.supported;
  }

  static latest(): FakeHls {
    const hls = FakeHls.instances.at(-1);
    if (hls === undefined) throw new Error("No player of hls.js was built.");
    return hls;
  }

  /** The playlist it was told to load, and the element it plays in. */
  source: string | null = null;
  media: HTMLMediaElement | null = null;
  destroyed = false;
  private readonly listeners = new Map<string, Array<(name: string, data: unknown) => void>>();

  constructor(readonly config: unknown) {
    FakeHls.instances.push(this);
  }

  on(name: string, listener: (name: string, data: unknown) => void): void {
    this.listeners.set(name, [...(this.listeners.get(name) ?? []), listener]);
  }

  loadSource(source: string): void {
    this.source = source;
  }

  attachMedia(media: HTMLMediaElement): void {
    this.media = media;
  }

  destroy(): void {
    this.destroyed = true;
  }

  private emit(name: string, data: unknown): void {
    for (const listener of this.listeners.get(name) ?? []) listener(name, data);
  }

  /** The playlist was read. */
  parsed(): void {
    this.emit(EVENTS.MANIFEST_PARSED, {});
  }

  /** An error of hls.js: one it gives up on, unless `fatal` is false. */
  fail(fatal = true): void {
    this.emit(EVENTS.ERROR, { fatal, type: "networkError", details: "manifestLoadError" });
  }
}

/** What `import("hls.js")` gives. */
export const hlsModule = { default: FakeHls };

/** Before each test: no player, and a browser hls.js can use. */
export function resetHls(): void {
  FakeHls.instances = [];
  FakeHls.supported = true;
}

export type FakeBrowser = {
  /** `navigator.vendor`: "Apple Computer, Inc." is Apple's engine. Chrome's when left out. */
  vendor?: string;
  /** Whether the `<video>` element claims the type of a playlist. */
  claimsPlaylist?: boolean;
  /** Media Source Extensions, as every browser but an iPhone's has them. */
  mediaSource?: boolean;
  /** `ManagedMediaSource`, which Apple's engine has. */
  managedMediaSource?: boolean;
  /** What `play()` answers: "refused" is a browser that starts no picture by itself. */
  play?: "started" | "refused";
};

/**
 * Says which browser the test runs in, for the rest of the test. Left alone, jsdom names
 * Apple as its vendor, claims no type and has no Media Source Extensions: a test of the
 * player always names its browser. Chrome is the default: Media Source Extensions and a
 * claim of its own, which the rule passes over.
 *
 * Returns the spies on the element: `play`, `pause` and `load`.
 */
export function installBrowser({
  vendor = "Google Inc.",
  claimsPlaylist = true,
  mediaSource = true,
  managedMediaSource = false,
  play = "started",
}: FakeBrowser = {}) {
  vi.spyOn(window.navigator, "vendor", "get").mockReturnValue(vendor);
  vi.spyOn(HTMLMediaElement.prototype, "canPlayType").mockImplementation((type) =>
    claimsPlaylist && type === "application/vnd.apple.mpegurl" ? "maybe" : "",
  );
  if (mediaSource) vi.stubGlobal("MediaSource", class {});
  if (managedMediaSource) vi.stubGlobal("ManagedMediaSource", class {});

  return {
    play: vi
      .spyOn(HTMLMediaElement.prototype, "play")
      .mockImplementation(() =>
        play === "started"
          ? Promise.resolve()
          : Promise.reject(new DOMException("The request is not allowed.", "NotAllowedError")),
      ),
    pause: vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {}),
    load: vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => {}),
  };
}

/** Safari, and every browser on an iPhone: Apple's engine plays a playlist itself. */
export const SAFARI: FakeBrowser = {
  vendor: "Apple Computer, Inc.",
  claimsPlaylist: true,
  mediaSource: false,
  managedMediaSource: true,
};

/** The picture moved: the playback is at `seconds` now. */
export function showFrame(video: HTMLMediaElement, seconds: number): void {
  Object.defineProperty(video, "currentTime", { configurable: true, value: seconds });
  act(() => {
    video.dispatchEvent(new Event("timeupdate"));
  });
}

/** The element says what a browser's does: `pause`, `play`, `error`. */
export function tell(video: HTMLMediaElement, name: "pause" | "play" | "error"): void {
  act(() => {
    video.dispatchEvent(new Event(name));
  });
}

/**
 * The chat panel is another feature's. In its place stands this: it shows what it was
 * given, and a field that tells the section of its focus as the panel's composer does.
 *
 *     vi.mock("@/features/chat", async () => (await import("./testing")).chatFeature);
 */
function ChatPanelStandIn({ marketId, broadcastId, fill, onComposerFocus }: ChatPanelProps) {
  return (
    <div
      data-testid="chat-panel"
      data-market={marketId}
      data-broadcast={broadcastId}
      data-fill={fill === true ? "yes" : "no"}
    >
      <input
        aria-label="Chat stand-in field"
        onFocus={() => onComposerFocus?.(true)}
        onBlur={() => onComposerFocus?.(false)}
      />
    </div>
  );
}

export const chatFeature = { ChatPanel: ChatPanelStandIn };
