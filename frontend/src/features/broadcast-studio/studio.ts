import type { ApiError } from "@/lib/api/errors";
import type { BroadcastConfig } from "@/lib/api/schema";

import {
  type Camera,
  type CaptureRefusal,
  closeCamera,
  openCamera,
  startRecorder,
  stopRecorder,
} from "./capture";
import { Uploader } from "./uploader";

/**
 * What this tab does about the live stream. Whether a live stream is on is the API's to
 * say; this is only what the page in hand has done.
 *
 * - `idle`: nothing yet. The studio may hold a camera for its preview.
 * - `live`: the recorder runs and its pieces go up.
 * - `paused`: the page was sending and stopped: the camera went away, the recorder gave
 *   up, or the upload had to start over too often.
 * - `over`: the live stream this page sent to has ended, by a tap here, by the server, or
 *   because the API refuses its pieces.
 */
export type StudioPhase = "idle" | "live" | "paused" | "over";

export type StudioState = {
  phase: StudioPhase;
  /** The camera the studio holds: what its preview shows and its recorder records. */
  camera: Camera | null;
  /** The browser is being asked for the camera. */
  asking: boolean;
  /** What the browser said when it was asked last, while it holds none. */
  refusal: CaptureRefusal | null;
  /** More than half of what may wait to be sent is waiting. */
  slow: boolean;
  /** The portal answered an upload with its 401: its sign-in has ended. */
  signedOut: boolean;
  /** Why the API refuses the pieces: shown with the ended state. */
  refused: ApiError | null;
};

const AT_REST: StudioState = {
  phase: "idle",
  camera: null,
  asking: false,
  refusal: null,
  slow: false,
  signedOut: false,
  refused: null,
};

/**
 * The studio of one page (DESIGN.md §13, "Live stream studio"), without React: the camera,
 * the recorder and the upload, and what became of them. The screen reads its state, and
 * tells it what the user and the API said.
 *
 * It decides nothing about the live stream: it starts to send when the screen says that
 * the API answered LIVE, and it stops when the screen says that the stream is over. What
 * it does decide is its own: a camera that goes away, a recorder that gives up and an
 * upload that keeps starting over pause it, and a piece the API refuses ends it.
 *
 * No picture and no sound is kept: a piece is held in memory until it is taken, and
 * dropped with the part it belongs to.
 */
export class Studio {
  private readonly config: BroadcastConfig;
  private state: StudioState = AT_REST;
  private readonly listeners = new Set<() => void>();
  private recorder: MediaRecorder | null = null;
  private uploader: Uploader | null = null;
  /** Counts the requests for the camera: the answer to one that was overtaken is let go. */
  private asks = 0;
  /** The width and the height of the picture, as the preview reported them last. */
  private shape: string | null = null;

  constructor(config: BroadcastConfig) {
    this.config = config;
  }

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  getState = (): StudioState => this.state;

  /**
   * Asks the browser for the camera and the microphone, unless the studio holds them.
   * Resolves with the camera, or with null when the browser refused or the request was
   * overtaken; the state then says why.
   */
  async open(): Promise<Camera | null> {
    if (this.state.camera !== null) return this.state.camera;
    const ask = ++this.asks;
    this.set({ asking: true, refusal: null });

    const opened = await openCamera(this.config);
    if (ask !== this.asks) {
      // The studio was left, or asked again, while the browser made up its mind.
      if (typeof opened !== "string") closeCamera(opened);
      return null;
    }
    if (typeof opened === "string") {
      this.set({ asking: false, refusal: opened });
      return null;
    }

    for (const track of opened.stream.getTracks()) {
      track.addEventListener("ended", () => this.lost(opened));
    }
    this.shape = null;
    this.set({ asking: false, camera: opened });
    return opened;
  }

  /** Lets the camera of the preview go, unless the studio is sending from it. */
  release(): void {
    if (this.state.phase === "live") return;
    this.asks += 1;
    this.letCameraGo();
    this.set({ asking: false, refusal: null });
  }

  /**
   * The API says the live stream is LIVE: the recorder starts, and its pieces go up to
   * `broadcastId`, the id of that answer. The studio holds a camera by then.
   */
  begin(broadcastId: string): void {
    if (this.state.camera === null) return;
    this.stopSending();
    this.uploader = new Uploader({
      broadcastId,
      config: this.config,
      onPart: () => this.record(),
      onPause: () => this.pause(),
      onStop: (error) => this.finish(error),
      onNotice: (notice) => this.set(notice),
    });
    this.set({ phase: "live", slow: false, signedOut: false, refused: null });
    this.record();
  }

  /** "Resume": asks for the camera again and goes on sending, in another part. */
  async resume(broadcastId: string): Promise<void> {
    const camera = await this.open();
    if (camera !== null) this.begin(broadcastId);
  }

  /**
   * The preview reports the size of its picture. A picture that changes its shape, as that
   * of a phone that is turned does, opens another part.
   */
  reshaped(width: number, height: number): void {
    const shape = `${width}x${height}`;
    const changed = this.shape !== null && this.shape !== shape;
    this.shape = shape;
    // A decoder that began with one size does not take another inside the same part.
    if (changed && this.state.phase === "live") this.uploader?.anotherPart("asked");
  }

  /**
   * The live stream is over: nothing more is sent and the camera is let go. `refused` is
   * the API's refusal of a piece, when that is how the page learned it.
   */
  finish(refused: ApiError | null = null): void {
    this.asks += 1;
    this.stopSending();
    this.letCameraGo();
    this.set({
      phase: "over",
      asking: false,
      refusal: null,
      slow: false,
      signedOut: false,
      refused,
    });
  }

  /** The screen is left: everything is let go, and the studio is at rest again. */
  close(): void {
    this.asks += 1;
    this.stopSending();
    this.letCameraGo();
    this.set(AT_REST);
  }

  /* ---- What happens by itself ---- */

  /** Starts a run of the recorder for the part the upload is at. */
  private record(): void {
    const { camera } = this.state;
    const uploader = this.uploader;
    if (camera === null || uploader === null) return;
    if (this.recorder !== null) stopRecorder(this.recorder);
    try {
      const recorder: MediaRecorder = startRecorder(camera, this.config, {
        // A piece of a run that was ended belongs to the part before: it is dropped.
        onPiece: (piece) => {
          if (this.recorder === recorder) uploader.push(piece);
        },
        onFailure: () => {
          if (this.recorder === recorder) this.pause();
        },
      });
      this.recorder = recorder;
    } catch {
      // A camera the browser cannot record: the same as a recorder that gave up.
      this.recorder = null;
      this.pause();
    }
  }

  /** A track of the camera ended: the camera went away, or the browser took it back. */
  private lost(camera: Camera): void {
    if (this.state.camera !== camera) return;
    if (this.state.phase === "live") {
      this.pause();
      return;
    }
    this.letCameraGo();
    this.set({ refusal: "missing" });
  }

  /** Stops sending and lets the camera go. "Resume" asks for it again. */
  private pause(): void {
    if (this.state.phase !== "live") return;
    this.stopSending();
    this.letCameraGo();
    this.set({ phase: "paused", slow: false, signedOut: false });
  }

  private stopSending(): void {
    this.uploader?.stop();
    this.uploader = null;
    if (this.recorder !== null) stopRecorder(this.recorder);
    this.recorder = null;
  }

  private letCameraGo(): void {
    const { camera } = this.state;
    if (camera === null) return;
    closeCamera(camera);
    this.set({ camera: null });
  }

  private set(change: Partial<StudioState>): void {
    this.state = { ...this.state, ...change };
    for (const listener of [...this.listeners]) listener();
  }
}
