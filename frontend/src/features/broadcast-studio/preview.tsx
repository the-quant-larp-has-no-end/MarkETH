"use client";

import { useEffect, useEffectEvent, useRef } from "react";

/**
 * The preview (DESIGN.md §13, "Live stream studio"): the broadcaster's own picture, in the
 * 16:9 box of §12 on `--card`, fitted in whole, muted so that nobody hears themselves, and
 * without controls. It is named "Preview".
 */
export function Preview({
  stream,
  onPicture,
  onResize,
}: {
  stream: MediaStream;
  /** The preview shows a picture: the camera delivers. */
  onPicture?: () => void;
  /** The picture has a size, or changed it: a phone that was turned. */
  onResize?: (width: number, height: number) => void;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const resized = useEffectEvent((width: number, height: number) => onResize?.(width, height));

  useEffect(() => {
    const element = video.current;
    if (element === null) return;

    const report = () => resized(element.videoWidth, element.videoHeight);
    element.addEventListener("resize", report);
    element.srcObject = stream;
    void (async () => {
      try {
        await element.play();
      } catch {
        // Autoplay may be refused; the picture still starts with the first frame.
      }
    })();

    return () => {
      element.removeEventListener("resize", report);
      element.srcObject = null;
    };
  }, [stream]);

  return (
    <div data-slot="preview" className="aspect-video w-full overflow-hidden rounded-lg bg-card">
      <video
        ref={video}
        muted
        playsInline
        aria-label="Preview"
        className="size-full object-contain"
        onLoadedData={onPicture}
        onCanPlay={onPicture}
        onPlaying={onPicture}
      />
    </div>
  );
}
