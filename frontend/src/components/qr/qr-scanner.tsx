"use client";

import { useEffect, useEffectEvent, useRef, useState } from "react";

import { Banner } from "@/components/feedback";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type Camera = "starting" | "live" | "refused" | "insecure" | "unavailable";

const MESSAGES: Record<Exclude<Camera, "starting" | "live">, string> = {
  refused: "Camera access was refused. Allow it in your browser settings, or type the code.",
  insecure: "The camera needs a secure connection (HTTPS). Type the code instead.",
  unavailable: "No camera is available. Type the code instead.",
};

/** A frame is looked at this often at most; reading a QR costs time on an old phone. */
const DECODE_EVERY_MS = 120;
/** The longer side of the frame handed to jsQR. */
const DECODE_SIZE = 640;

type Decode = (video: HTMLVideoElement) => Promise<string | null>;

/**
 * Whatever decodes the picture is the app's own JavaScript: the browser's
 * `BarcodeDetector` where it reads QR codes, otherwise jsQR, loaded from the app's
 * origin on first use. No wasm, no worker, no remote script (the Content-Security-Policy
 * allows none).
 */
async function createDecoder(): Promise<Decode> {
  const Detector = window.BarcodeDetector;
  if (Detector !== undefined) {
    try {
      if ((await Detector.getSupportedFormats()).includes("qr_code")) {
        const detector = new Detector({ formats: ["qr_code"] });
        return async (video) => (await detector.detect(video))[0]?.rawValue ?? null;
      }
    } catch {
      // A detector that cannot say what it reads is not used.
    }
  }

  const { default: jsQR } = await import("jsqr");
  const canvas = document.createElement("canvas");
  const context = canvas.getContext("2d", { willReadFrequently: true });
  return async (video) => {
    if (context === null || video.videoWidth === 0 || video.videoHeight === 0) return null;
    const scale = Math.min(1, DECODE_SIZE / Math.max(video.videoWidth, video.videoHeight));
    canvas.width = Math.round(video.videoWidth * scale);
    canvas.height = Math.round(video.videoHeight * scale);
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    const frame = context.getImageData(0, 0, canvas.width, canvas.height);
    return (
      jsQR(frame.data, frame.width, frame.height, { inversionAttempts: "dontInvert" })?.data ?? null
    );
  };
}

function initialCamera(): Camera {
  if (typeof navigator === "undefined") return "starting";
  if (navigator.mediaDevices?.getUserMedia !== undefined) return "starting";
  return window.isSecureContext ? "unavailable" : "insecure";
}

export type QrScannerProps = {
  /**
   * Called once with what a QR held. The scanner then reads nothing more until `paused`
   * has been true and is false again: that is how the screen asks for the next scan.
   */
  onScan: (text: string) => void;
  /** True while the screen is busy with the last scan. The camera stays on. */
  paused?: boolean;
  /** The label of the typed alternative: "Type the code instead". */
  alternativeLabel: string;
  onAlternative: () => void;
  /** Told when the camera starts and stops showing a picture. */
  onLiveChange?: (live: boolean) => void;
  className?: string;
};

/**
 * The scanner (DESIGN.md §12): a full-width camera view with a square guide in
 * `--foreground` corners. It reports what it read once and pauses until the screen asks
 * for the next scan. It always shows a typed alternative, and says clearly when the
 * camera cannot be used: permission refused, no secure connection (the camera needs
 * HTTPS), or no camera at all.
 */
export function QrScanner({
  onScan,
  paused = false,
  alternativeLabel,
  onAlternative,
  onLiveChange,
  className,
}: QrScannerProps) {
  const video = useRef<HTMLVideoElement>(null);
  const [camera, setCamera] = useState<Camera>(initialCamera);
  const isPaused = useRef(paused);
  const reported = useRef(false);

  const report = useEffectEvent((text: string) => onScan(text));
  const liveChanged = useEffectEvent((live: boolean) => onLiveChange?.(live));

  useEffect(() => {
    isPaused.current = paused;
    // The screen is ready again: the next QR may be reported.
    if (!paused) reported.current = false;
  }, [paused]);

  useEffect(() => {
    if (navigator.mediaDevices?.getUserMedia === undefined) return;

    let stopped = false;
    let stream: MediaStream | null = null;
    let live = false;
    let cancelFrame = () => {};

    const start = async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: "environment" },
          audio: false,
        });
      } catch (error) {
        if (stopped) return;
        const denied =
          error instanceof DOMException &&
          (error.name === "NotAllowedError" || error.name === "SecurityError");
        setCamera(denied ? "refused" : "unavailable");
        return;
      }
      const element = video.current;
      if (stopped || element === null) {
        for (const track of stream.getTracks()) track.stop();
        return;
      }

      element.srcObject = stream;
      try {
        await element.play();
      } catch {
        // Autoplay may be refused; the picture still starts with the first frame.
      }
      if (stopped) return;
      live = true;
      setCamera("live");
      liveChanged(true);

      const decode = await createDecoder();
      let busy = false;
      let lookedAt = 0;

      const look = async (now: number) => {
        if (stopped) return;
        next();
        if (busy || isPaused.current || reported.current || now - lookedAt < DECODE_EVERY_MS)
          return;
        busy = true;
        lookedAt = now;
        try {
          const text = await decode(element);
          if (text && !stopped && !isPaused.current && !reported.current) {
            reported.current = true;
            report(text);
          }
        } catch {
          // A frame that cannot be read is skipped; the next one comes at once.
        } finally {
          busy = false;
        }
      };

      const next = () => {
        if ("requestVideoFrameCallback" in element) {
          const handle = element.requestVideoFrameCallback((now) => void look(now));
          cancelFrame = () => element.cancelVideoFrameCallback(handle);
        } else {
          const handle = window.requestAnimationFrame((now) => void look(now));
          cancelFrame = () => window.cancelAnimationFrame(handle);
        }
      };

      if (!stopped) next();
    };

    void start();

    return () => {
      stopped = true;
      cancelFrame();
      for (const track of stream?.getTracks() ?? []) track.stop();
      if (live) liveChanged(false);
    };
  }, []);

  const failed = camera === "refused" || camera === "insecure" || camera === "unavailable";

  return (
    <div data-slot="qr-scanner" className={cn("flex flex-col gap-4", className)}>
      {failed ? (
        <Banner>{MESSAGES[camera]}</Banner>
      ) : (
        <div className="relative aspect-square w-full overflow-hidden rounded-lg bg-card">
          <video
            ref={video}
            muted
            playsInline
            aria-label="Camera"
            className="size-full object-cover"
          />
          {/* The square guide: four corners. */}
          <div aria-hidden className="pointer-events-none absolute inset-[15%]">
            <span className="absolute top-0 left-0 size-8 rounded-tl-md border-t-4 border-l-4 border-foreground" />
            <span className="absolute top-0 right-0 size-8 rounded-tr-md border-t-4 border-r-4 border-foreground" />
            <span className="absolute bottom-0 left-0 size-8 rounded-bl-md border-b-4 border-l-4 border-foreground" />
            <span className="absolute right-0 bottom-0 size-8 rounded-br-md border-r-4 border-b-4 border-foreground" />
          </div>
        </div>
      )}
      <Button variant="secondary" onClick={onAlternative}>
        {alternativeLabel}
      </Button>
    </div>
  );
}
