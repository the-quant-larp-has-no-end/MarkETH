import Image from "next/image";
import type { CSSProperties } from "react";

import { cn } from "@/lib/utils";

const SIZES = {
  16: "size-4",
  20: "size-5",
  32: "size-8",
  64: "size-16",
} as const;

export type TokenIconSize = keyof typeof SIZES;

/**
 * The token icon (DESIGN.md §3): the pixel-art coffee bean centred on a `--foreground`
 * disc, the bean filling 70% of it. The bean's dark outline disappears on navy, so it
 * never appears without its disc. Decorative: an amount is read as its number and the
 * word "tokens" (`Amount`).
 *
 * 16 px beside `number` and `body-sm` figures, 20 px beside `number-lg`, 32 px beside
 * `display`, 64 px in the welcome dialog.
 */
export function TokenIcon({
  size = 16,
  className,
  style,
}: {
  size?: TokenIconSize;
  className?: string;
  /** Position and timing for the bean animations; never a colour. */
  style?: CSSProperties;
}) {
  return (
    <span
      aria-hidden
      data-slot="token-icon"
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-full bg-foreground",
        SIZES[size],
        className,
      )}
      style={style}
    >
      <Image
        src="/brand/token.png"
        alt=""
        width={256}
        height={256}
        className="size-[70%] pixelated"
      />
    </span>
  );
}
