import { cn } from "@/lib/utils";

import { BrandImage } from "./brand-image";
import type { BrandFile } from "./files";

const PLACES = {
  1: { file: "trophy-1", alt: "First place", rank: "1st" },
  2: { file: "trophy-2", alt: "Second place", rank: "2nd" },
  3: { file: "trophy-3", alt: "Third place", rank: "3rd" },
} as const satisfies Record<number, { file: BrandFile; alt: string; rank: string }>;

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
   * Fetch the image ahead of the other images of the screen: the trophies of the podium,
   * which are the largest thing the leaderboard opens with.
   */
  eager?: boolean;
  className?: string;
}) {
  const { file, alt } = PLACES[place];
  return (
    <BrandImage
      name={file}
      alt={alt}
      width={256}
      height={256}
      first={eager}
      className={cn("shrink-0 pixelated", SIZES[size], className)}
    />
  );
}
