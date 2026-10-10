import { vi } from "vitest";

function box(width: number, height: number): DOMRect {
  const edges = { x: 0, y: 0, top: 0, left: 0, right: width, bottom: height, width, height };
  return { ...edges, toJSON: () => edges };
}

/**
 * jsdom lays nothing out, and a chart without a size draws nothing. This gives the
 * container of every Recharts chart the size it has on a phone, so that its line, its
 * axes and its labels are in the document. Every other element measures 0 x 0, as it
 * does without this.
 *
 * Spies are restored after each test, so call it in `beforeEach`.
 */
export function sizeCharts(width = 343, height = 200): void {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (
    this: HTMLElement,
  ) {
    return this.classList.contains("recharts-responsive-container")
      ? box(width, height)
      : box(0, 0);
  });
}
