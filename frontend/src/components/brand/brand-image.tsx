"use client";

import Image from "next/image";

import { BRAND_FILES, type BrandFile } from "./files";
import { keepForSession } from "./session-copy";

/**
 * The `img` of a brand image. Every component of this folder shows its picture through
 * it, so that each brand image is fetched once per page session and is in its element
 * when that element is first painted (DESIGN.md §8):
 *
 * - its address is the one the build gives the file (`./files`);
 * - it is fetched when it mounts and decoded with the frame that paints it, never when
 *   it comes near the screen or a frame later;
 * - once it has loaded, the page keeps a copy of it (`./session-copy`).
 *
 * `data-brand` holds the name of the file. It is how the browser suite finds the brand
 * images of a screen.
 */
export function BrandImage({
  name,
  alt,
  width,
  height,
  first = false,
  className,
  "aria-hidden": ariaHidden,
}: {
  name: BrandFile;
  alt: string;
  /** The side lengths the `img` states, which keep its place before it is styled. */
  width: number;
  height: number;
  /** Fetch it ahead of the other images of the screen. */
  first?: boolean;
  className?: string;
  "aria-hidden"?: boolean;
}) {
  return (
    <Image
      src={BRAND_FILES[name]}
      alt={alt}
      aria-hidden={ariaHidden}
      width={width}
      height={height}
      loading="eager"
      decoding="sync"
      fetchPriority={first ? "high" : undefined}
      data-brand={name}
      className={className}
      onLoad={({ currentTarget }) => keepForSession(currentTarget.currentSrc || currentTarget.src)}
    />
  );
}
