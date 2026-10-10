import { EN_DASH } from "@/lib/format";

/**
 * Stands where the API has no figure (DESIGN.md §10, §15): an en dash to the eye, "none
 * yet" to a screen reader, which would otherwise read the dash. A win rate of a player
 * without a resolved market, a quote that has not been asked for, a tier a proposal has
 * not been given.
 */
export function Missing({ className }: { className?: string }) {
  return (
    <span data-slot="missing" className={className}>
      <span aria-hidden>{EN_DASH}</span>
      <span className="sr-only">none yet</span>
    </span>
  );
}
