import { cn } from "@/lib/utils";

import { BrandImage } from "./brand-image";
import type { BrandFile } from "./files";

const LOCKUPS = {
  /** The shapes in `--foreground`, for the navy surfaces of the app. */
  screen: "logo-lockup",
  /** The shapes in `--ink`, for paper: the printable QR sheet only. */
  paper: "logo-lockup-dark",
} as const satisfies Record<string, BrandFile>;

/**
 * The logo with name (DESIGN.md §3): the cup with the rising arrow, followed by
 * "markETH". The name is part of the image, so no text is ever placed beside it. 28 px
 * tall in the top bar, 48 px on the screens that stand in front of the app.
 */
export function Logo({
  size = "bar",
  on = "screen",
  className,
}: {
  size?: "bar" | "large";
  /** What the logo stands on. `paper` is for the printable QR sheet and nothing else. */
  on?: keyof typeof LOCKUPS;
  className?: string;
}) {
  return (
    <BrandImage
      name={LOCKUPS[on]}
      alt="MarkETH"
      width={716}
      height={168}
      className={cn("w-auto shrink-0", size === "bar" ? "h-7" : "h-12", className)}
    />
  );
}

/** The logo alone, for where only a square fits. */
export function LogoMark({ className }: { className?: string }) {
  return (
    <BrandImage
      name="logo-mark"
      alt="MarkETH"
      width={320}
      height={320}
      className={cn("size-8 shrink-0", className)}
    />
  );
}
