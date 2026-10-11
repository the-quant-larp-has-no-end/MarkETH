import type { BroadcastConfig } from "@/lib/api/schema";

/**
 * The camera, the microphone and the recorder of the studio (DESIGN.md §13, "Live stream
 * studio"): what the browser is asked for, and what it says when it cannot.
 *
 * Every figure comes from `broadcast` of `GET /config`: the picture the camera is asked
 * for, what the recorder may spend, the types it may record as and the length of a piece.
 */

/** Why this browser cannot broadcast right now: the four banners of the studio. */
export type CaptureRefusal =
  /** The user or the browser said no to the camera or the microphone. */
  | "refused"
  /** The page has no secure connection, so the browser hands out no camera. */
  | "insecure"
  /** No camera or no microphone, or something else holds it. */
  | "missing"
  /** The browser records none of the types the server takes. */
  | "unsupported";

/** `capture.refusal` is a key, not copy: these are its sentences (DESIGN.md §13). */
export const REFUSALS: Record<CaptureRefusal, string> = {
  refused: "Camera and microphone access was refused. Allow both in your browser settings.",
  insecure: "The camera needs a secure connection (HTTPS).",
  missing: "No camera or microphone is available.",
  unsupported: "This browser cannot broadcast. Use a current Chrome, Edge, Firefox or Safari.",
};

/** What the studio records from: the camera's stream and the type its recorder writes. */
export type Camera = { stream: MediaStream; mimeType: string };

const MP4 = "video/mp4";

/** Apple's engine: Safari, and every browser on an iPhone or an iPad. */
function isAppleEngine(): boolean {
  return navigator.vendor.startsWith("Apple");
}

/**
 * The type the recorder is asked for: the first of `capture_mime_types` it supports, in
 * the order `/config` gives them, with two rules about MP4.
 *
 * - On Apple's engine the MP4 types are tried first: H.264 is encoded by the hardware
 *   there, and VP8 is not.
 * - Everywhere else an MP4 type that names no codecs is not tried. What a browser puts
 *   into such a file is its own choice (Chrome: VP9), and the server takes VP8, H.264,
 *   Opus and AAC alone.
 *
 * Null: this browser records nothing the server takes.
 */
export function chooseMimeType(types: readonly string[]): string | null {
  if (typeof MediaRecorder === "undefined") return null;
  const isMp4 = (type: string) => type.startsWith(MP4);
  const inOrder = isAppleEngine()
    ? [...types.filter(isMp4), ...types.filter((type) => !isMp4(type))]
    : types.filter((type) => !isMp4(type) || type.includes("codecs="));
  return inOrder.find((type) => MediaRecorder.isTypeSupported(type)) ?? null;
}

/**
 * What the camera and the microphone are asked for. Each figure is a wish, not a
 * condition: a camera that cannot do it hands over what it has, and the server scales the
 * picture. The camera that faces the user is the one a broadcaster talks into.
 */
export function cameraConstraints(config: BroadcastConfig): MediaStreamConstraints {
  return {
    video: {
      height: { ideal: config.capture_height },
      frameRate: { ideal: config.capture_fps },
      facingMode: { ideal: "user" },
    },
    audio: { echoCancellation: true, noiseSuppression: true },
  };
}

function refusalOf(error: unknown): CaptureRefusal {
  const name = error instanceof DOMException ? error.name : "";
  return name === "NotAllowedError" || name === "SecurityError" ? "refused" : "missing";
}

/**
 * Asks for the camera and the microphone. The answer is the camera, or the reason the
 * studio shows in its place. A browser that cannot record is told so before it is asked
 * for its camera: no permission is asked for that would lead nowhere.
 */
export async function openCamera(config: BroadcastConfig): Promise<Camera | CaptureRefusal> {
  if (navigator.mediaDevices?.getUserMedia === undefined) {
    return window.isSecureContext ? "missing" : "insecure";
  }
  const mimeType = chooseMimeType(config.capture_mime_types);
  if (mimeType === null) return "unsupported";

  try {
    const stream = await navigator.mediaDevices.getUserMedia(cameraConstraints(config));
    return { stream, mimeType };
  } catch (error) {
    return refusalOf(error);
  }
}

/** Lets the camera and the microphone go: the light of the camera goes out. */
export function closeCamera(camera: Camera): void {
  for (const track of camera.stream.getTracks()) track.stop();
}

export type RecorderHandlers = {
  /** The recorder hands a piece over. */
  onPiece: (piece: Blob) => void;
  /** The recorder gave up. */
  onFailure: () => void;
};

/**
 * Starts a recorder on the camera: one run, whose pieces are one part of the upload. It
 * hands a piece over every `chunk_ms`, and the first of them carries the header a decoder
 * needs, which is why what comes after a gap is another run and not the next piece.
 *
 * Throws what the browser throws for a camera it cannot record.
 */
export function startRecorder(
  camera: Camera,
  config: BroadcastConfig,
  handlers: RecorderHandlers,
): MediaRecorder {
  const recorder = new MediaRecorder(camera.stream, {
    mimeType: camera.mimeType,
    videoBitsPerSecond: config.capture_video_bits_per_second,
    audioBitsPerSecond: config.capture_audio_bits_per_second,
  });
  recorder.addEventListener("dataavailable", (event) => handlers.onPiece(event.data));
  recorder.addEventListener("error", () => handlers.onFailure());
  recorder.start(config.chunk_ms);
  return recorder;
}

/** Ends a recorder's run. What it still hands over belongs to nobody. */
export function stopRecorder(recorder: MediaRecorder): void {
  if (recorder.state !== "inactive") recorder.stop();
}
