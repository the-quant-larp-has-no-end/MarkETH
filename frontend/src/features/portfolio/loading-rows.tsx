import { RowSkeletons } from "@/components/rows";

/**
 * A list of the Portfolio that is still loading (DESIGN.md §13): five skeleton rows. The
 * blocks say nothing to a screen reader, so the word does.
 */
export function LoadingRows() {
  return (
    <div data-slot="loading-rows" role="status">
      <span className="sr-only">Loading</span>
      <RowSkeletons count={5} />
    </div>
  );
}
