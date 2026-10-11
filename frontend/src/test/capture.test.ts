import { Blob as NodeBlob } from "node:buffer";

import { expect, test, vi } from "vitest";

import { config } from "./fixtures";
import {
  FakeMediaRecorder,
  FakeMediaStream,
  installFakeCapture,
  piece,
  pieceOfSize,
  showPicture,
} from "./capture";

const TYPES = config().broadcast.capture_mime_types;
const firstSupported = () => TYPES.find((type) => MediaRecorder.isTypeSupported(type));

test("a page gets a camera and a microphone, and the fake knows what was asked for", async () => {
  const capture = installFakeCapture();
  const constraints = { video: { height: { ideal: 720 } }, audio: true };

  const stream = await window.navigator.mediaDevices.getUserMedia(constraints);

  expect(capture.requests).toEqual([constraints]);
  expect(stream).toBe(capture.stream());
  expect(stream.getVideoTracks()).toHaveLength(1);
  expect(stream.getAudioTracks()).toHaveLength(1);
  expect(stream.active).toBe(true);
  expect("mediaDevices" in navigator).toBe(true);
  expect(window.isSecureContext).toBe(true);
});

test.each([
  ["refused", "NotAllowedError"],
  ["missing", "NotFoundError"],
  ["busy", "NotReadableError"],
] as const)("access %s rejects the request with %s", async (access, name) => {
  const capture = installFakeCapture({ access });

  await expect(navigator.mediaDevices.getUserMedia({ video: true })).rejects.toMatchObject({
    name,
  });
  expect(capture.streams).toEqual([]);

  capture.setAccess("granted");
  await expect(navigator.mediaDevices.getUserMedia({ video: true })).resolves.toBeInstanceOf(
    FakeMediaStream,
  );
});

test("a page without a secure connection has no mediaDevices", () => {
  installFakeCapture({ secure: false });

  expect(navigator.mediaDevices).toBeUndefined();
  expect("mediaDevices" in navigator).toBe(false);
  expect(window.isSecureContext).toBe(false);
});

test("the browser is named: Chrome unless the test says otherwise, never jsdom's own vendor", () => {
  installFakeCapture();
  expect(navigator.vendor).toBe("Google Inc.");
  expect(firstSupported()).toBe("video/webm;codecs=vp8,opus");
  // What is not the fake's is the page's own navigator.
  expect(navigator.userAgent).toContain("jsdom");

  installFakeCapture({ browser: "safari" });
  expect(navigator.vendor).toMatch(/^Apple/);
  expect(firstSupported()).toBe("video/mp4;codecs=avc1.42E01F,mp4a.40.2");

  installFakeCapture({ browser: "firefox" });
  expect(TYPES.filter((type) => MediaRecorder.isTypeSupported(type))).toEqual([
    "video/webm;codecs=vp8,opus",
    "video/webm",
  ]);
});

test("a recorder that takes none of the types, and a browser without a recorder", () => {
  installFakeCapture({ supported: [] });
  expect(firstSupported()).toBeUndefined();
  vi.unstubAllGlobals();

  installFakeCapture({ recorder: false });
  expect(typeof MediaRecorder).toBe("undefined");
  expect("MediaRecorder" in window).toBe(false);
});

test("a recorder hands over the pieces the test emits, with what the app asked of it", async () => {
  const capture = installFakeCapture();
  const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
  const recorder = new MediaRecorder(stream, {
    mimeType: "video/webm;codecs=vp8,opus",
    videoBitsPerSecond: 1800000,
    audioBitsPerSecond: 64000,
  });
  const pieces: Blob[] = [];
  recorder.addEventListener("dataavailable", (event) => pieces.push(event.data));

  expect(() => capture.recorder().emit(piece("early"))).toThrow(/not recording/);
  recorder.start(1000);
  capture.recorder().emit(piece("one"));
  capture.recorder().emit(piece(""));
  capture.recorder().emit(pieceOfSize(5));

  expect(capture.recorder()).toBe(recorder);
  expect(capture.recorder().timeslice).toBe(1000);
  expect(capture.recorder().options.videoBitsPerSecond).toBe(1800000);
  expect(recorder.state).toBe("recording");
  expect(pieces.map((blob) => blob.size)).toEqual([3, 0, 5]);
  expect(await pieces[0]!.text()).toBe("one");
  expect(() => new MediaRecorder(stream, { mimeType: "video/x-matroska" })).toThrow();
});

test("a recorder that stops hands over what it still holds, and then says that it stopped", async () => {
  const capture = installFakeCapture();
  const recorder = new MediaRecorder(await navigator.mediaDevices.getUserMedia({ video: true }));
  const seen: string[] = [];
  recorder.ondataavailable = (event) => seen.push(`piece of ${event.data.size}`);
  recorder.onstop = () => seen.push("stop");
  recorder.start(1000);

  recorder.stop();

  // Not yet: a browser tells both a moment after the call.
  expect(recorder.state).toBe("inactive");
  expect(seen).toEqual([]);
  await Promise.resolve();
  expect(seen).toEqual(["piece of 4", "stop"]);

  // A recorder on the same stream is another recorder, as when a part begins.
  const next = new MediaRecorder(capture.stream() as unknown as MediaStream);
  next.start(1000);
  expect(capture.recorders).toHaveLength(2);
  expect(capture.recorder()).toBe(next);
  (next as unknown as FakeMediaRecorder).rest = null;
  next.onstop = () => seen.push("stop without a piece");
  next.stop();
  await Promise.resolve();
  expect(seen.at(-1)).toBe("stop without a piece");
});

test("a camera that goes away ends its track; a track the app lets go ends without a word", async () => {
  const capture = installFakeCapture();
  const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
  const ended = vi.fn();
  stream.getVideoTracks()[0]!.addEventListener("ended", ended);
  stream.getAudioTracks()[0]!.onended = ended;

  capture.stream().video.end();
  expect(ended).toHaveBeenCalledTimes(1);
  expect(capture.stream().released).toBe(false);

  for (const track of stream.getTracks()) track.stop();
  expect(ended).toHaveBeenCalledTimes(1);
  expect(capture.stream().released).toBe(true);
});

test("a recorder that gives up says so and records nothing more", async () => {
  const capture = installFakeCapture();
  const recorder = new MediaRecorder(await navigator.mediaDevices.getUserMedia({ video: true }));
  const failed = vi.fn();
  recorder.onerror = failed;
  recorder.start(1000);

  capture.recorder().fail();

  expect(failed).toHaveBeenCalledTimes(1);
  expect(recorder.state).toBe("inactive");
});

test("a preview plays without jsdom's complaint, and the test says when it has a picture", async () => {
  installFakeCapture();
  const video = document.createElement("video");
  const playing = vi.fn();
  video.addEventListener("playing", playing);

  await expect(video.play()).resolves.toBeUndefined();
  showPicture(video);

  expect(playing).toHaveBeenCalledTimes(1);
});

test("a piece is a Blob the test's Request sends with its bytes", async () => {
  installFakeCapture();
  const body = new Blob([piece("one"), piece("two")]);

  const request = new Request("http://localhost/upload", { method: "POST", body });

  expect(body.size).toBe(6);
  expect(await request.text()).toBe("onetwo");
  expect(await body.slice(0, 4).text()).toBe("onet");
});

test("what the fake put in place is gone with its test", () => {
  // The test before this one installed the fake. The set-up took it away again.
  expect(typeof MediaRecorder).toBe("undefined");
  expect(typeof MediaStream).toBe("undefined");
  expect(navigator.mediaDevices).toBeUndefined();
  expect(navigator.vendor).not.toBe("Google Inc.");
  expect(new Blob(["one"])).not.toBeInstanceOf(NodeBlob);
});
