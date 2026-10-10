import Image from "next/image";

import { cn } from "@/lib/utils";

const PLACES = {
  1: { src: "/brand/trophy-1.png", alt: "First place", rank: "1st" },
  2: { src: "/brand/trophy-2.png", alt: "Second place", rank: "2nd" },
  3: { src: "/brand/trophy-3.png", alt: "Third place", rank: "3rd" },
} as const;

const SIZES = {
  24: "size-6",
  72: "size-18",
  96: "size-24",
} as const;

export type TrophyPlace = keyof typeof PLACES;

/** The rank a trophy always comes with, as text: "1st", "2nd", "3rd". */
export function rankLabel(place: TrophyPlace): string {
  return PLACES[place].rank;
}

/**
 * A pixel trophy (DESIGN.md §3): gold, silver or bronze for the first three places of a
 * "Highest first" leaderboard, and for nothing else. It sits directly on the surface,
 * with no disc, tile or ring behind it, and always next to its rank as text.
 *
 * 96 px for first place and 72 px for the others on the podium; 24 px beside a username.
 */
export function Trophy({
  place,
  size,
  eager = false,
  className,
}: {
  place: TrophyPlace;
  size: keyof typeof SIZES;
  /**
   * Fetch the image at once instead of when it comes near the screen: the trophies of
   * the podium, which are the largest thing the leaderboard opens with.
   */
  eager?: boolean;
  className?: string;
}) {
  const { src, alt } = PLACES[place];
  return (
    <Image
      src={src}
      alt={alt}
      width={256}
      height={256}
      loading={eager ? "eager" : "lazy"}
      className={cn("shrink-0 pixelated", SIZES[size], className)}
    />
  );
}
