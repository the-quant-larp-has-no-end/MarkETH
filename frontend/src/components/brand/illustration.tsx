import {
  AlarmClockIcon,
  CableCarIcon,
  CoffeeIcon,
  type LucideIcon,
  PresentationIcon,
  UniversityIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";

import { BrandImage } from "./brand-image";
import type { BrandFile } from "./files";

type Slot = {
  /** What stands in the slot until the pixel art is drawn: a 32 px lucide icon. */
  standIn: LucideIcon;
  /**
   * The drawing, once it exists in `public/brand/pixel/`: its name in `./files`, where
   * the file is imported, and the side of its pixel grid (16 or 24), so that it is shown
   * at a whole multiple of that grid.
   */
  art: { file: BrandFile; grid: 16 | 24 } | null;
};

/**
 * The six pixel-art spot illustrations of DESIGN.md §3 and where they stand. None is
 * drawn yet, and no screen waits for one: to put a drawing in, set its `art` here.
 */
const SLOTS = {
  /** Empty portfolio, empty wallet activity. */
  "empty-cup": { standIn: CoffeeIcon, art: null },
  /** Page-level loading, the "not found" screen. */
  polybahn: { standIn: CableCarIcon, art: null },
  /** Profile set-up footer, empty Markets list. */
  dome: { standIn: UniversityIcon, art: null },
  /** Voucher issued. */
  "steaming-cup": { standIn: CoffeeIcon, art: null },
  /** Propose market: the empty list of "My proposals". */
  blackboard: { standIn: PresentationIcon, art: null },
  /** A closed market waiting for its result. */
  "alarm-clock": { standIn: AlarmClockIcon, art: null },
} satisfies Record<string, Slot>;

export type IllustrationName = keyof typeof SLOTS;

/** The side a drawing is shown at: the whole multiple of its grid nearest to 48 px. */
const SHOWN = { 16: 48, 24: 48 } as const;

/**
 * One illustration slot. Decorative: the sentence beside it says what there is to say.
 * Colours are `--muted-foreground` for a stand-in; a drawing brings its own few.
 */
export function Illustration({ name, className }: { name: IllustrationName; className?: string }) {
  const slot: Slot = SLOTS[name];

  if (slot.art !== null) {
    const side = SHOWN[slot.art.grid];
    return (
      <BrandImage
        name={slot.art.file}
        alt=""
        aria-hidden
        width={side}
        height={side}
        className={cn("shrink-0 pixelated", className)}
      />
    );
  }

  const StandIn = slot.standIn;
  return <StandIn aria-hidden className={cn("size-8 shrink-0 text-muted-foreground", className)} />;
}
