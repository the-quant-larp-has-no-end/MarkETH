import { Blob as NodeBlob } from "node:buffer";

import { vi } from "vitest";

/**
 * A camera, a microphone and a recorder the test drives, which jsdom does not have:
 * `navigator.mediaDevices.getUserMedia`, `MediaStream` and `MediaRecorder`, as the studio
 * of a live stream uses them (DESIGN.md §13, "Live stream studio").
 *
 *     const capture = installFakeCapture();
 *     renderWithProviders(<BroadcastStudioScreen marketId={MARKET_ID} />);
 *     showPicture(screen.getByLabelText("Preview"));         // the preview has a picture
 *     await user.click(screen.getByRole("button", { name: "Go live" }));
 *     capture.recorder().emit(piece("one"));                  // the recorder hands a piece over
 *     capture.stream().video.end();                           // the camera went away
 *     capture.recorder().fail();                              // the recorder gave up
 *
 * What the browser refuses, one line each:
 *
 *     installFakeCapture({ access: "refused" })    // the user said no to camera or microphone
 *     installFakeCapture({ access: "missing" })    // no camera or no microphone
 *     installFakeCapture({ secure: false })        // no secure connection: no mediaDevices
 *     installFakeCapture({ supported: [] })        // a recorder that takes none of the types
 *     installFakeCapture({ recorder: false })      // a browser without MediaRecorder
 *
 * Three things a test has to know:
 *
 * - **The browser is named.** jsdom calls its vendor "Apple Computer, Inc.", which is how
 *   Apple's engine is told from the others. The fake is Chrome unless `browser` says
 *   otherwise, with the vendor and the recorder types of that browser.
 * - **A recorder that stops hands over what it still holds**, as a browser's does: `stop()`
 *   is followed by one more `dataavailable` (`rest`, a few bytes unless the test sets it)
 *   and then by `stop`, both a moment later. A piece of a recorder the studio has let go
 *   belongs to no part.
 * - **`Blob` is Node's while the fake is installed.** The `Request` of the test
 *   environment is Node's and sends no other kind, so a piece that goes up arrives at
 *   `mockApi` with its bytes: `api.lastCall(route)?.rawBody` is the text of `piece("one")`.
 *
 * Everything is put back after the test: the globals by `vi.unstubAllGlobals()` of
 * `src/test/setup.ts`, the spies on the `<video>` element by `restoreMocks` of
 * `vitest.config.ts`.
 */

/** What a request for the camera and the microphone gets. */
export type CaptureAccess =
  /** A stream with one video and one audio track. */
  | "granted"
  /** `NotAllowedError`: the user or the browser refused. */
  | "refused"
  /** `NotFoundError`: there is no camera, or no microphone. */
  | "missing"
  /** `NotReadableError`: the camera is there and something else holds it. */
  | "busy";

export type FakeBrowser = "chrome" | "firefox" | "safari";

/** How each browser names its vendor, and which of the contract's types its recorder takes. */
const BROWSERS: Record<FakeBrowser, { vendor: string; supported: readonly string[] }> = {
  chrome: {
    vendor: "Google Inc.",
    supported: [
      "video/webm;codecs=vp8,opus",
      "video/webm",
      "video/mp4;codecs=avc1.42E01F,mp4a.40.2",
      "video/mp4",
    ],
  },
  firefox: { vendor: "", supported: ["video/webm;codecs=vp8,opus", "video/webm"] },
  safari: {
    vendor: "Apple Computer, Inc.",
    supported: ["video/mp4;codecs=avc1.42E01F,mp4a.40.2", "video/mp4"],
  },
};

export type FakeCaptureOptions = {
  /** Whose engine this is: its vendor and its recorder types. Chrome when left out. */
  browser?: FakeBrowser;
  /** The types `MediaRecorder.isTypeSupported` accepts, in place of the browser's own. */
  supported?: readonly string[];
  /** What a request for the camera gets. "granted" when left out. */
  access?: CaptureAccess;
  /** False: the page has no secure connection, so `navigator.mediaDevices` does not exist. */
  secure?: boolean;
  /** False: the browser has no `MediaRecorder`. */
  recorder?: boolean;
};

const ERRORS: Record<Exclude<CaptureAccess, "granted">, string> = {
  refused: "NotAllowedError",
  missing: "NotFoundError",
  busy: "NotReadableError",
};

let counter = 0;

function fire(target: EventTarget, name: string, fields: Record<string, unknown> = {}): void {
  const event = Object.assign(new Event(name), fields);
  const handler = (target as unknown as Record<string, unknown>)[`on${name}`];
  target.dispatchEvent(event);
  if (typeof handler === "function") handler.call(target, event);
}

/** One track of a fake stream. */
export class FakeMediaStreamTrack extends EventTarget {
  readonly id = `track-${++counter}`;
  readonly label: string;
  enabled = true;
  muted = false;
  readyState: "live" | "ended" = "live";
  onended: ((event: Event) => void) | null = null;

  constructor(readonly kind: "audio" | "video") {
    super();
    this.label = kind === "video" ? "Fake camera" : "Fake microphone";
  }

  /** What the app calls to let the track go. As in a browser, no `ended` is fired for it. */
  stop(): void {
    this.readyState = "ended";
  }

  /** The device went away, or the browser's own control ended it: `ended` is fired. */
  end(): void {
    if (this.readyState === "ended") return;
    this.readyState = "ended";
    fire(this, "ended");
  }

  getSettings(): MediaTrackSettings {
    return this.kind === "video" ? { width: 1280, height: 720, frameRate: 24 } : {};
  }
}

/** The stream `getUserMedia` hands out: one video track and one audio track. */
export class FakeMediaStream extends EventTarget {
  readonly id = `stream-${++counter}`;
  private readonly tracks: FakeMediaStreamTrack[];

  constructor(tracks?: FakeMediaStream | FakeMediaStreamTrack[]) {
    super();
    this.tracks =
      tracks === undefined
        ? [new FakeMediaStreamTrack("video"), new FakeMediaStreamTrack("audio")]
        : tracks instanceof FakeMediaStream
          ? tracks.getTracks()
          : [...tracks];
  }

  getTracks(): FakeMediaStreamTrack[] {
    return [...this.tracks];
  }

  getVideoTracks(): FakeMediaStreamTrack[] {
    return this.tracks.filter((track) => track.kind === "video");
  }

  getAudioTracks(): FakeMediaStreamTrack[] {
    return this.tracks.filter((track) => track.kind === "audio");
  }

  /** The camera's track. */
  get video(): FakeMediaStreamTrack {
    return this.getVideoTracks()[0]!;
  }

  /** The microphone's track. */
  get audio(): FakeMediaStreamTrack {
    return this.getAudioTracks()[0]!;
  }

  /** As in a browser: true while a track is live. */
  get active(): boolean {
    return this.tracks.some((track) => track.readyState === "live");
  }

  /** True once every track was let go or went away: the camera's light is off. */
  get released(): boolean {
    return !this.active;
  }
}

/** The recorder the app makes on a stream. The test hands its pieces over with `emit`. */
export class FakeMediaRecorder extends EventTarget {
  /** The types this browser's recorder takes. Set by `installFakeCapture`. */
  static supported: readonly string[] = [];
  static instances: FakeMediaRecorder[] = [];

  static isTypeSupported(type: string): boolean {
    return FakeMediaRecorder.supported.includes(type);
  }

  readonly stream: FakeMediaStream;
  /** What the app asked for: the type and the two bit rates. */
  readonly options: MediaRecorderOptions;
  readonly mimeType: string;
  readonly videoBitsPerSecond: number | undefined;
  readonly audioBitsPerSecond: number | undefined;
  state: "inactive" | "recording" | "paused" = "inactive";
  /** The `timeslice` of the latest `start()`: the length of a piece in milliseconds. */
  timeslice: number | undefined;
  /** How often `start()` was called. */
  starts = 0;
  /**
   * What `stop()` hands over as its last piece. A browser's recorder always hands one
   * over, empty when it holds nothing; null here sends none.
   */
  rest: Blob | null = piece("rest");

  ondataavailable: ((event: Event & { data: Blob }) => void) | null = null;
  onstart: ((event: Event) => void) | null = null;
  onstop: ((event: Event) => void) | null = null;
  onpause: ((event: Event) => void) | null = null;
  onresume: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;

  constructor(stream: FakeMediaStream, options: MediaRecorderOptions = {}) {
    super();
    if (options.mimeType !== undefined && !FakeMediaRecorder.isTypeSupported(options.mimeType)) {
      throw new DOMException(`The type ${options.mimeType} is not supported.`, "NotSupportedError");
    }
    this.stream = stream;
    this.options = options;
    this.mimeType = options.mimeType ?? FakeMediaRecorder.supported[0] ?? "";
    this.videoBitsPerSecond = options.videoBitsPerSecond;
    this.audioBitsPerSecond = options.audioBitsPerSecond;
    FakeMediaRecorder.instances.push(this);
  }

  start(timeslice?: number): void {
    if (this.state !== "inactive") {
      throw new DOMException("The recorder is recording already.", "InvalidStateError");
    }
    this.state = "recording";
    this.timeslice = timeslice;
    this.starts += 1;
    fire(this, "start");
  }

  /** Stops at once; the last piece and `stop` follow a moment later, as in a browser. */
  stop(): void {
    if (this.state === "inactive") {
      throw new DOMException("The recorder is not recording.", "InvalidStateError");
    }
    this.state = "inactive";
    const rest = this.rest;
    queueMicrotask(() => {
      if (rest !== null) fire(this, "dataavailable", { data: rest });
      fire(this, "stop");
    });
  }

  pause(): void {
    if (this.state !== "recording") return;
    this.state = "paused";
    fire(this, "pause");
  }

  resume(): void {
    if (this.state !== "paused") return;
    this.state = "recording";
    fire(this, "resume");
  }

  requestData(): void {
    if (this.state === "inactive") {
      throw new DOMException("The recorder is not recording.", "InvalidStateError");
    }
  }

  /* ---- What the test does to the recorder ---- */

  /** The recorder hands a piece over: `dataavailable`. An empty piece is one of size 0. */
  emit(data: Blob): void {
    if (this.state === "inactive") {
      throw new Error("This recorder is not recording: the app stopped it or never started it.");
    }
    fire(this, "dataavailable", { data });
  }

  /** The recorder gave up: `error`, and it is inactive from here on. */
  fail(name = "UnknownError"): void {
    this.state = "inactive";
    fire(this, "error", { error: new DOMException("The recorder failed.", name) });
  }
}

/**
 * A piece as a recorder hands it over, with this text as its bytes, so that a test reads
 * what went up as text. `piece("")` is the empty piece a recorder sometimes hands over.
 */
export function piece(text: string, type = "video/webm"): Blob {
  return new NodeBlob([text], { type }) as unknown as Blob;
}

/** A piece of this many bytes, for the rules about sizes. */
export function pieceOfSize(bytes: number, type = "video/webm"): Blob {
  return piece(".".repeat(bytes), type);
}

/**
 * Tells a `<video>` element that it has a picture, which jsdom never does: the events a
 * browser fires when a stream or a playlist starts to play.
 */
export function showPicture(video: HTMLVideoElement): void {
  for (const name of ["loadedmetadata", "loadeddata", "canplay", "playing"]) {
    video.dispatchEvent(new Event(name));
  }
}

export type FakeCapture = {
  /** The constraints of every request for the camera, in order. */
  readonly requests: MediaStreamConstraints[];
  /** Every stream that was handed out, in order. */
  readonly streams: FakeMediaStream[];
  /** Every recorder the app made, in order. */
  readonly recorders: FakeMediaRecorder[];
  /** The stream handed out last. */
  stream(): FakeMediaStream;
  /** The recorder the app made last. */
  recorder(): FakeMediaRecorder;
  /** What requests for the camera get from here on. */
  setAccess(access: CaptureAccess): void;
};

/** Puts the fakes in place of the browser's camera and recorder for the rest of the test. */
export function installFakeCapture(options: FakeCaptureOptions = {}): FakeCapture {
  const browser = BROWSERS[options.browser ?? "chrome"];
  let access: CaptureAccess = options.access ?? "granted";
  const requests: MediaStreamConstraints[] = [];
  const streams: FakeMediaStream[] = [];

  FakeMediaRecorder.supported = options.supported ?? browser.supported;
  FakeMediaRecorder.instances = [];

  const mediaDevices = {
    getUserMedia: async (constraints: MediaStreamConstraints = {}): Promise<FakeMediaStream> => {
      requests.push(constraints);
      if (access !== "granted") {
        throw new DOMException("The camera is not available.", ERRORS[access]);
      }
      const stream = new FakeMediaStream();
      streams.push(stream);
      return stream;
    },
    enumerateDevices: async () =>
      access === "missing"
        ? []
        : [
            { kind: "videoinput", deviceId: "camera", groupId: "fake", label: "Fake camera" },
            {
              kind: "audioinput",
              deviceId: "microphone",
              groupId: "fake",
              label: "Fake microphone",
            },
          ],
  };
  const secure = options.secure ?? true;

  // The navigator of the page, with a vendor and with the devices of a secure page.
  const real = window.navigator;
  const own: Record<PropertyKey, unknown> = {
    vendor: browser.vendor,
    mediaDevices: secure ? mediaDevices : undefined,
  };
  vi.stubGlobal(
    "navigator",
    new Proxy(real, {
      get(target, key) {
        if (key in own) return own[key];
        const value: unknown = Reflect.get(target, key, target);
        return typeof value === "function" ? value.bind(target) : value;
      },
      has: (target, key) => (key in own ? own[key] !== undefined : key in target),
    }),
  );
  vi.stubGlobal("isSecureContext", secure);
  vi.stubGlobal("MediaStream", FakeMediaStream);
  vi.stubGlobal("MediaStreamTrack", FakeMediaStreamTrack);
  // jsdom has no recorder: a browser without one is jsdom as it is.
  if (options.recorder !== false) vi.stubGlobal("MediaRecorder", FakeMediaRecorder);
  vi.stubGlobal("Blob", NodeBlob);

  // A preview plays without a sound; jsdom plays nothing and says so on the console.
  vi.spyOn(HTMLMediaElement.prototype, "play").mockImplementation(async () => {});
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
  vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => {});

  return {
    requests,
    streams,
    get recorders() {
      return FakeMediaRecorder.instances;
    },
    stream() {
      const stream = streams.at(-1);
      if (stream === undefined) throw new Error("The app has not been given a camera.");
      return stream;
    },
    recorder() {
      const recorder = FakeMediaRecorder.instances.at(-1);
      if (recorder === undefined) throw new Error("The app has not made a recorder.");
      return recorder;
    },
    setAccess(next) {
      access = next;
    },
  };
}
