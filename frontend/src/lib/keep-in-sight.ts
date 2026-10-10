/**
 * Scrolls a strip that scrolls sideways just far enough that `chosen` lies inside it: the
 * pressed chip of a row of chips, the active tab of a row of tabs (DESIGN.md §12). Only
 * the strip moves, never the page, and nothing moves when the element is in sight.
 */
export function keepInSight(strip: Element | null, chosen: Element | null | undefined): void {
  if (strip === null || chosen === null || chosen === undefined) return;
  const item = chosen.getBoundingClientRect();
  const view = strip.getBoundingClientRect();
  if (item.left < view.left) strip.scrollLeft -= view.left - item.left;
  else if (item.right > view.right) strip.scrollLeft += item.right - view.right;
}
