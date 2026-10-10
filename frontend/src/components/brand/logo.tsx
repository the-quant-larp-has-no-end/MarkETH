import Image from "next/image";

import { cn } from "@/lib/utils";

const LOCKUPS = {
  /** The shapes in `--foreground`, for the navy surfaces of the app. */
  screen: "/brand/logo-lockup.png",
  /** The shapes in `--ink`, for paper: the printable QR sheet only. */
  paper: "/brand/logo-lockup-dark.png",
} as const;

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
    <Image
      src={LOCKUPS[on]}
      alt="MarkETH"
      width={716}
      height={168}
      loading="eager"
      className={cn("w-auto shrink-0", size === "bar" ? "h-7" : "h-12", className)}
    />
  );
}

/** The logo alone, for where only a square fits. */
export function LogoMark({ className }: { className?: string }) {
  return (
    <Image
      src="/brand/logo-mark.png"
      alt="MarkETH"
      width={320}
      height={320}
      className={cn("size-8 shrink-0", className)}
    />
  );
}
