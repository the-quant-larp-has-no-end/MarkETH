import { describe, expect, test, vi } from "vitest";

import { installFakeCapture, piece } from "@/test/capture";
import { broadcastConfig } from "@/test/fixtures";

import {
  type Camera,
  cameraConstraints,
  chooseMimeType,
  closeCamera,
  openCamera,
  REFUSALS,
  startRecorder,
  stopRecorder,
} from "./capture";

const WEBM = "video/webm;codecs=vp8,opus";
const MP4 = "video/mp4;codecs=avc1.42E01F,mp4a.40.2";
const TYPES = broadcastConfig().capture_mime_types;

async function camera(): Promise<Camera> {
  const opened = await openCamera(broadcastConfig());
  if (typeof opened === "string") throw new Error(`The camera was refused: ${opened}`);
  return opened;
}

describe("what the camera is asked for", () => {
  test("comes from /config: the height and the frames a second, as wishes", async () => {
    const capture = installFakeCapture();

    await openCamera(broadcastConfig({ capture_height: 480, capture_fps: 15 }));

    expect(capture.requests).toEqual([
      {
        video: {
          height: { ideal: 480 },
          frameRate: { ideal: 15 },
          facingMode: { ideal: "user" },
        },
        audio: { echoCancellation: true, noiseSuppression: true },
      },
    ]);
  });

  test("the camera and the microphone, in one request", () => {
    const { video, audio } = cameraConstraints(broadcastConfig());

    expect(video).toMatchObject({ height: { ideal: 720 }, frameRate: { ideal: 24 } });
    expect(audio).toBeTruthy();
  });
});

describe("the type the recorder writes", () => {
  test.each([
    ["chrome", WEBM],
    ["firefox", WEBM],
    ["safari", MP4],
  ] as const)("in %s is %s", (browser, type) => {
    installFakeCapture({ browser });

    expect(chooseMimeType(TYPES)).toBe(type);
  });

  test("is the first of the list the recorder takes, in the order /config gives", () => {
    installFakeCapture({ supported: ["video/webm", MP4] });

    expect(chooseMimeType(TYPES)).toBe("video/webm");
  });

  test("on Apple's engine is MP4 first, also where the recorder takes WebM", () => {
    installFakeCapture({ browser: "safari", supported: TYPES });

    expect(chooseMimeType(TYPES)).toBe(MP4);
  });

  test("on Apple's engine is WebM where the recorder takes nothing else", () => {
    installFakeCapture({ browser: "safari", supported: [WEBM] });

    expect(chooseMimeType(TYPES)).toBe(WEBM);
  });

  test("outside Apple's engine is never an MP4 that names no codecs", () => {
    installFakeCapture({ supported: ["video/mp4"] });

    expect(chooseMimeType(TYPES)).toBeNull();
  });

  test("on Apple's engine may be the MP4 that names no codecs", () => {
    installFakeCapture({ browser: "safari", supported: ["video/mp4"] });

    expect(chooseMimeType(TYPES)).toBe("video/mp4");
  });

  test("is none where the recorder takes none of the list", () => {
    installFakeCapture({ supported: ["video/x-matroska"] });

    expect(chooseMimeType(TYPES)).toBeNull();
  });
});

describe("a camera that is handed out", () => {
  test("comes with the type its recorder writes", async () => {
    const capture = installFakeCapture();

    const opened = await camera();

    expect(opened.mimeType).toBe(WEBM);
    expect(opened.stream).toBe(capture.stream());
  });

  test("is let go track by track", async () => {
    const capture = installFakeCapture();
    const opened = await camera();
    expect(capture.stream().released).toBe(false);

    closeCamera(opened);

    expect(capture.stream().released).toBe(true);
  });
});

describe("what the browser refuses", () => {
  test.each([
    ["refused", "refused"],
    ["missing", "missing"],
    // A camera something else holds is not available either.
    ["busy", "missing"],
  ] as const)("a request that is %s is the banner of '%s'", async (access, refusal) => {
    installFakeCapture({ access });

    expect(await openCamera(broadcastConfig())).toBe(refusal);
  });

  test("a page without a secure connection has no camera to ask for", async () => {
    const capture = installFakeCapture({ secure: false });

    expect(await openCamera(broadcastConfig())).toBe("insecure");
    expect(capture.requests).toEqual([]);
  });

  test("a recorder that takes none of the types cannot broadcast, and the camera is not asked for", async () => {
    const capture = installFakeCapture({ supported: [] });

    expect(await openCamera(broadcastConfig())).toBe("unsupported");
    expect(capture.requests).toEqual([]);
  });

  test("a browser without a recorder cannot broadcast", async () => {
    const capture = installFakeCapture({ recorder: false });

    expect(await openCamera(broadcastConfig())).toBe("unsupported");
    expect(capture.requests).toEqual([]);
  });

  test("each has its sentence", () => {
    expect(REFUSALS).toEqual({
      refused: "Camera and microphone access was refused. Allow both in your browser settings.",
      insecure: "The camera needs a secure connection (HTTPS).",
      missing: "No camera or microphone is available.",
      unsupported: "This browser cannot broadcast. Use a current Chrome, Edge, Firefox or Safari.",
    });
  });
});

describe("a recorder's run", () => {
  test("is started with the type, the two bit rates and the length of a piece of /config", async () => {
    const capture = installFakeCapture();
    const config = broadcastConfig({
      chunk_ms: 500,
      capture_video_bits_per_second: 900_000,
      capture_audio_bits_per_second: 32_000,
    });

    startRecorder(await camera(), config, { onPiece: vi.fn(), onFailure: vi.fn() });

    const recorder = capture.recorder();
    expect(recorder.stream).toBe(capture.stream());
    expect(recorder.options).toEqual({
      mimeType: WEBM,
      videoBitsPerSecond: 900_000,
      audioBitsPerSecond: 32_000,
    });
    expect(recorder.state).toBe("recording");
    expect(recorder.timeslice).toBe(500);
  });

  test("hands its pieces over as they are", async () => {
    const capture = installFakeCapture();
    const onPiece = vi.fn();
    startRecorder(await camera(), broadcastConfig(), { onPiece, onFailure: vi.fn() });
    const one = piece("one");

    capture.recorder().emit(one);

    expect(onPiece).toHaveBeenCalledExactlyOnceWith(one);
  });

  test("says when the recorder gave up", async () => {
    const capture = installFakeCapture();
    const onFailure = vi.fn();
    startRecorder(await camera(), broadcastConfig(), { onPiece: vi.fn(), onFailure });

    capture.recorder().fail();

    expect(onFailure).toHaveBeenCalledOnce();
  });

  test("is ended once, also when it has ended by itself", async () => {
    const capture = installFakeCapture();
    const recorder = startRecorder(await camera(), broadcastConfig(), {
      onPiece: vi.fn(),
      onFailure: vi.fn(),
    });

    stopRecorder(recorder);
    expect(capture.recorder().state).toBe("inactive");

    expect(() => stopRecorder(recorder)).not.toThrow();
  });
});
