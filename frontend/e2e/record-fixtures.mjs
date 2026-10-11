/**
 * Records the fixtures of the media box: what a browser's MediaRecorder hands over, piece
 * by piece, exactly as a studio sends it. The image test of the box
 * (`backend/tests/integration/test_mediabox_image.py`, `uv run pytest -m media` in
 * `backend/`) feeds them to the real ffmpeg.
 *
 *   node e2e/record-fixtures.mjs                        chrome-webm and chrome-mp4
 *   node e2e/record-fixtures.mjs firefox-webm           the fixtures named
 *   node e2e/record-fixtures.mjs --by-hand safari-mp4   serves the page and waits for a person
 *
 * | Fixture        | Browser                                                 | Picture and sound                |
 * | -------------- | ------------------------------------------------------- | -------------------------------- |
 * | `chrome-webm`  | the Chrome that is installed                            | its stand-in camera, microphone  |
 * | `chrome-mp4`   | the Chrome that is installed                            | its stand-in camera, microphone  |
 * | `firefox-webm` | Playwright's Firefox (`npx playwright install firefox`) | its stand-in camera, microphone  |
 * | `webkit-mp4`   | Playwright's WebKit (`npx playwright install webkit`)   | a drawn picture, no sound        |
 * | `safari-mp4`   | Safari, by hand                                         | the Mac's camera and microphone  |
 *
 * A fixture is the directory `backend/tests/fixtures/broadcast/<fixture>/`: one file per
 * Blob of `dataavailable`, unchanged and in order (`000.bin`, `001.bin`, ...), and
 * `manifest.json`, which says what the browser reported. A run replaces the fixtures it
 * names and touches no other; a run that fails leaves the fixture as it was.
 *
 * By hand: the script prints an address and waits. A person opens it in the browser the
 * fixture is named after, on this machine, and taps "Record"; the page says when it is
 * done. `safari-mp4` is recorded this way, once, on a Mac.
 *
 * This is no spec: Playwright's runner does not collect it, and it needs no stack.
 */

import { mkdirSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium, firefox, webkit } from "@playwright/test";

const FIXTURES_ROOT = fileURLToPath(
  new URL("../../backend/tests/fixtures/broadcast/", import.meta.url),
);

const WEBM = "video/webm;codecs=vp8,opus";
const MP4 = "video/mp4;codecs=avc1.42E01F,mp4a.40.2";

const FIXTURES = {
  "chrome-webm": { browser: "chrome", mime: WEBM, source: "camera" },
  "chrome-mp4": { browser: "chrome", mime: MP4, source: "camera" },
  "firefox-webm": { browser: "firefox", mime: WEBM, source: "camera" },
  // Playwright's WebKit has no stand-in camera, and its sound does not start without a tap.
  "webkit-mp4": { browser: "webkit", mime: MP4, source: "canvas" },
  "safari-mp4": { browser: "safari", mime: MP4, source: "camera" },
};
const RECORDED_WITHOUT_A_PERSON = ["chrome-webm", "chrome-mp4"];

/** What every fixture is recorded with: a small picture, a piece a second, ten seconds. */
const RECORDING = {
  width: 320,
  height: 180,
  fps: 24,
  videoBitsPerSecond: 250_000,
  audioBitsPerSecond: 64_000,
  timesliceMs: 1000,
  seconds: 10,
};
/** How long a browser has for a recording, and a person for theirs. */
const BROWSER_LIMIT_MS = (RECORDING.seconds + 30) * 1000;
const PERSON_LIMIT_MS = 10 * 60 * 1000;

/**
 * The page that records. It keeps nothing and repacks nothing: every Blob goes to the
 * script as it came, one request after the other, and a report follows the last one.
 */
const PAGE = `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Record a fixture</title>
  </head>
  <body>
    <button id="record" type="button">Record</button>
    <canvas id="drawn" width="${RECORDING.width}" height="${RECORDING.height}"></canvas>
    <video id="preview" muted playsinline width="${RECORDING.width}"></video>
    <pre id="said"></pre>
    <script>
      const RECORDING = ${JSON.stringify(RECORDING)};
      const query = new URLSearchParams(location.search);
      const say = (line) => {
        document.getElementById("said").textContent += line + "\\n";
      };

      // A picture that moves, for a browser without a stand-in camera.
      function draw(canvas) {
        const context = canvas.getContext("2d");
        let frame = 0;
        setInterval(() => {
          context.fillStyle = "#1d2733";
          context.fillRect(0, 0, canvas.width, canvas.height);
          context.fillStyle = "hsl(" + ((frame * 3) % 360) + " 70% 45%)";
          context.fillRect(0, 0, canvas.width, canvas.height * 0.2);
          context.fillStyle = "#f2c14e";
          context.fillRect((frame * 4) % canvas.width, canvas.height * 0.4, 24, 24);
          context.fillStyle = "#ffffff";
          context.font = "20px monospace";
          context.fillText("frame " + frame, 12, canvas.height - 16);
          frame += 1;
        }, 1000 / RECORDING.fps);
        return canvas.captureStream(RECORDING.fps);
      }

      function capture() {
        if (query.get("source") === "canvas") return draw(document.getElementById("drawn"));
        return navigator.mediaDevices.getUserMedia({
          video: {
            width: { ideal: RECORDING.width },
            height: { ideal: RECORDING.height },
            frameRate: { ideal: RECORDING.fps, max: 30 },
          },
          audio: true,
        });
      }

      async function record() {
        const stream = await capture();
        const preview = document.getElementById("preview");
        preview.srcObject = stream;
        preview.play().catch(() => {});
        // A camera says what it delivers; a drawn picture is as large as its canvas.
        const said = stream.getVideoTracks()[0].getSettings();
        const drawn = query.get("source") === "canvas";
        const report = {
          userAgent: navigator.userAgent,
          asked: query.get("mime"),
          picture: {
            width: said.width || (drawn ? RECORDING.width : null),
            height: said.height || (drawn ? RECORDING.height : null),
            frameRate: said.frameRate || (drawn ? RECORDING.fps : null),
          },
          sound: stream.getAudioTracks().length > 0,
          pieces: [],
        };
        const recorder = new MediaRecorder(stream, {
          mimeType: query.get("mime"),
          videoBitsPerSecond: RECORDING.videoBitsPerSecond,
          audioBitsPerSecond: RECORDING.audioBitsPerSecond,
        });
        const began = performance.now();
        let sending = Promise.resolve();
        recorder.addEventListener("dataavailable", (event) => {
          if (event.data.size === 0) return;
          const index = report.pieces.length;
          report.pieces.push({ bytes: event.data.size, at_ms: Math.round(performance.now() - began) });
          sending = sending.then(() => fetch("piece?index=" + index, { method: "POST", body: event.data }));
        });
        recorder.addEventListener("error", (event) => {
          report.error = String(event.error || event);
        });
        const stopped = new Promise((resolve) => recorder.addEventListener("stop", resolve));
        recorder.start(RECORDING.timesliceMs);
        report.recorded = recorder.mimeType;
        say("recording " + recorder.mimeType + " for " + RECORDING.seconds + " s");
        await new Promise((resolve) => setTimeout(resolve, RECORDING.seconds * 1000));
        recorder.stop();
        await stopped;
        await sending;
        stream.getTracks().forEach((track) => track.stop());
        await fetch("done", { method: "POST", body: JSON.stringify(report) });
        say("done: " + report.pieces.length + " pieces");
      }

      function run() {
        document.getElementById("record").disabled = true;
        record().catch(async (error) => {
          say(String(error));
          await fetch("done", { method: "POST", body: JSON.stringify({ error: String(error) }) });
        });
      }

      document.getElementById("record").addEventListener("click", run);
      if (query.get("auto") === "1") run();
    </script>
  </body>
</html>
`;

function body(request) {
  return new Promise((resolve, reject) => {
    const parts = [];
    request.on("data", (part) => parts.push(part));
    request.on("end", () => resolve(Buffer.concat(parts)));
    request.on("error", reject);
  });
}

/** Serves the page on the loopback and collects what it posts. */
async function listen() {
  const pieces = [];
  let finish;
  const done = new Promise((resolve) => {
    finish = resolve;
  });
  const server = createServer(async (request, response) => {
    const url = new URL(request.url ?? "/", "http://localhost");
    if (request.method === "POST" && url.pathname === "/piece") {
      pieces[Number(url.searchParams.get("index"))] = await body(request);
      response.writeHead(204).end();
    } else if (request.method === "POST" && url.pathname === "/done") {
      finish(JSON.parse((await body(request)).toString("utf8")));
      response.writeHead(204).end();
    } else if (request.method === "GET" && url.pathname === "/") {
      response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" }).end(PAGE);
    } else {
      response.writeHead(404).end();
    }
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return { server, pieces, done, port: server.address().port };
}

function launch(browser) {
  if (browser === "chrome") {
    return chromium.launch({
      channel: "chrome",
      args: [
        "--use-fake-device-for-media-stream",
        "--use-fake-ui-for-media-stream",
        "--autoplay-policy=no-user-gesture-required",
      ],
    });
  }
  if (browser === "firefox") {
    return firefox.launch({
      firefoxUserPrefs: {
        "media.navigator.streams.fake": true,
        "media.navigator.permission.disabled": true,
      },
    });
  }
  if (browser === "webkit") return webkit.launch();
  throw new Error(`${browser} is recorded by hand: --by-hand`);
}

function within(promise, milliseconds) {
  return Promise.race([
    promise,
    new Promise((resolve) => setTimeout(() => resolve({ error: "timed out" }), milliseconds)),
  ]);
}

function save(name, fixture, version, report, pieces) {
  const directory = join(FIXTURES_ROOT, name);
  mkdirSync(directory, { recursive: true });
  for (const file of readdirSync(directory)) {
    if (file.endsWith(".bin") || file === "manifest.json") rmSync(join(directory, file));
  }
  const files = pieces.map((piece, index) => {
    const file = `${String(index).padStart(3, "0")}.bin`;
    writeFileSync(join(directory, file), piece);
    return { file, ...report.pieces[index] };
  });
  const manifest = {
    fixture: name,
    browser: version,
    user_agent: report.userAgent,
    asked: fixture.mime,
    recorded: report.recorded,
    source: fixture.source,
    width: report.picture.width,
    height: report.picture.height,
    frame_rate: report.picture.frameRate,
    sound: report.sound,
    timeslice_ms: RECORDING.timesliceMs,
    video_bits_per_second: RECORDING.videoBitsPerSecond,
    audio_bits_per_second: RECORDING.audioBitsPerSecond,
    pieces: files,
  };
  writeFileSync(join(directory, "manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`);
  return manifest;
}

async function record(name, byHand) {
  const fixture = FIXTURES[name];
  const { server, pieces, done, port } = await listen();
  const query = new URLSearchParams({ mime: fixture.mime, source: fixture.source });
  let browser = null;
  let version = fixture.browser;
  let report;
  try {
    if (byHand) {
      console.log(`${name}: open http://localhost:${port}/?${query} and tap "Record"`);
      report = await within(done, PERSON_LIMIT_MS);
    } else {
      browser = await launch(fixture.browser);
      version = `${fixture.browser} ${browser.version()}`;
      const page = await browser.newPage();
      query.set("auto", "1");
      await page.goto(`http://127.0.0.1:${port}/?${query}`);
      report = await within(done, BROWSER_LIMIT_MS);
    }
  } finally {
    await browser?.close();
    server.close();
  }
  const whole = pieces.length > 0 && !pieces.includes(undefined);
  if (report.error !== undefined || !whole || pieces.length !== report.pieces.length) {
    throw new Error(`${name}: ${report.error ?? "pieces are missing"}`);
  }
  if (!report.picture.height) {
    throw new Error(`${name}: the browser did not say how large its picture is`);
  }
  const manifest = save(name, fixture, version, report, pieces);
  const bytes = pieces.reduce((sum, piece) => sum + piece.length, 0);
  console.log(
    `${name}: ${pieces.length} pieces, ${bytes} bytes, ${manifest.recorded}, ` +
      `${manifest.width} x ${manifest.height}, ${manifest.sound ? "with sound" : "no sound"}`,
  );
}

async function main() {
  const words = process.argv.slice(2);
  const byHand = words.includes("--by-hand");
  const asked = words.filter((word) => word !== "--by-hand");
  const names = asked.length > 0 ? asked : RECORDED_WITHOUT_A_PERSON;
  const unknown = names.filter((name) => !(name in FIXTURES));
  if (unknown.length > 0 || (byHand && asked.length === 0)) {
    throw new Error(
      `Usage: node e2e/record-fixtures.mjs [--by-hand] [${Object.keys(FIXTURES).join(" | ")}] ...`,
    );
  }
  for (const name of names) {
    await record(name, byHand);
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
});
