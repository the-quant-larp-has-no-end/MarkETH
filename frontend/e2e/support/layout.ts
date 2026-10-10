import { expect, type Page } from "@playwright/test";

/**
 * The two things every spec asserts of every screen, at both widths: nothing lies over
 * anything else, and the page does not scroll sideways.
 *
 * What "lies over" is measured on. A piece is a control (a link, a button, a field) with
 * its box, or a run of text with the boxes of its lines. Two pieces overlap when their
 * boxes share more than `SLACK` px in both directions. Pieces of which one contains the
 * other are one thing, and so is a control that lies whole inside another (the button in
 * a field, the "Yes" on a card that is a link).
 *
 * What is pinned (`position: fixed` or `sticky`: the top bar, the tab bar, a pinned
 * button) lies over what scrolls under it, by design. It counts where the page cannot
 * move out of its way: a bar in the upper half of the screen with the page at its top, a
 * bar in the lower half with the page at its end. The page is measured at both.
 *
 * Left out: what is `aria-hidden` or `inert` (the page behind an open sheet), the inside
 * of an `<svg>` (a chart lays out its own labels), a toast (DESIGN §12: it stands over
 * the bottom of the screen for four seconds, and a person's first visit after the seed
 * shows their notices as toasts), and what the caller names in `ignore`.
 * A surface without text of its own (the background of a card, a picture) is no piece:
 * the review of the screenshots is where those are seen. A scroll area inside the page
 * is measured where it stands, because the helper scrolls the page and not the area; so
 * what is pinned inside something pinned (the button row of a sheet) is not measured
 * against the rest of that sheet.
 */
export type LayoutOptions = {
  /** Selectors whose elements, with everything inside, are no pieces. */
  ignore?: readonly string[];
};

/** Line boxes of text that stands close reach a few px into each other without touching. */
const SLACK = 4;

type Edge = "top" | "bottom";

/** Runs in the page: it can use nothing of this file but its argument. */
function overlaps({
  edge,
  ignore,
  slack,
}: {
  edge: Edge;
  ignore: readonly string[];
  slack: number;
}): string[] {
  type Box = { left: number; top: number; right: number; bottom: number };
  type Piece = {
    element: Element;
    control: boolean;
    pinned: Element | null;
    boxes: Box[];
    name: string;
  };

  const CONTROLS =
    'a[href], button, input:not([type="hidden"]), select, textarea, summary, ' +
    '[role="button"], [role="link"], [role="tab"], [role="switch"], [role="checkbox"], ' +
    '[role="radio"], [role="slider"], [role="combobox"], [role="option"], [role="menuitem"]';
  const TOASTS = "[data-sonner-toaster]";
  const NO_PIECE = ['[aria-hidden="true"]', "[inert]", "svg", TOASTS, ...ignore].join(", ");
  const screenWidth = document.documentElement.clientWidth;
  const screenHeight = window.innerHeight;

  /**
   * What of `box` shows: inside the screen, and inside `clipper` and every ancestor of it
   * that clips what it holds.
   */
  const shown = (box: Box, clipper: Element | null): Box | null => {
    let { left, top, right, bottom } = box;
    left = Math.max(left, 0);
    top = Math.max(top, 0);
    right = Math.min(right, screenWidth);
    bottom = Math.min(bottom, screenHeight);
    for (let parent = clipper; parent !== null; parent = parent.parentElement) {
      const style = getComputedStyle(parent);
      if (style.overflowX === "visible" && style.overflowY === "visible") continue;
      const frame = parent.getBoundingClientRect();
      if (style.overflowX !== "visible") {
        left = Math.max(left, frame.left);
        right = Math.min(right, frame.right);
      }
      if (style.overflowY !== "visible") {
        top = Math.max(top, frame.top);
        bottom = Math.min(bottom, frame.bottom);
      }
    }
    // Two px at least: what is there for a screen reader alone is one px wide and tall.
    return right - left >= 2 && bottom - top >= 2 ? { left, top, right, bottom } : null;
  };

  const pinnedOf = (element: Element): Element | null => {
    for (let current: Element | null = element; current !== null; current = current.parentElement) {
      const position = getComputedStyle(current).position;
      if (position === "fixed" || position === "sticky") return current;
    }
    return null;
  };

  const nameOf = (element: Element): string => {
    const text = (element.textContent ?? "").replace(/\s+/g, " ").trim();
    const said =
      text || element.getAttribute("aria-label") || element.getAttribute("name") || element.id;
    const tag = `<${element.tagName.toLowerCase()}>`;
    return said ? `${tag} "${said.length > 40 ? `${said.slice(0, 40)}…` : said}"` : tag;
  };

  const pieces: Piece[] = [];
  for (const element of document.body.querySelectorAll("*")) {
    if (element.closest(NO_PIECE) !== null) continue;
    if (!element.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) continue;
    const control = element.matches(CONTROLS);
    const boxes: Box[] = [];
    if (control) {
      const box = shown(element.getBoundingClientRect(), element.parentElement);
      if (box !== null) boxes.push(box);
    } else {
      // The lines of a text are cut by the element that holds it too: what is written
      // for a screen reader alone stands in a box of one px.
      for (const node of element.childNodes) {
        if (node.nodeType !== Node.TEXT_NODE || (node.textContent ?? "").trim() === "") continue;
        const range = document.createRange();
        range.selectNodeContents(node);
        for (const line of range.getClientRects()) {
          const box = shown(line, element);
          if (box !== null) boxes.push(box);
        }
      }
    }
    if (boxes.length > 0) {
      pieces.push({ element, control, pinned: pinnedOf(element), boxes, name: nameOf(element) });
    }
  }

  const inside = (inner: Box, outer: Box): boolean =>
    inner.left >= outer.left &&
    inner.right <= outer.right &&
    inner.top >= outer.top &&
    inner.bottom <= outer.bottom;

  /** Whether what is pinned to `bar` is in the way of the page at this edge of it. */
  const inTheWay = (bar: Element): boolean => {
    const frame = bar.getBoundingClientRect();
    const upperHalf = (frame.top + frame.bottom) / 2 < screenHeight / 2;
    return upperHalf === (edge === "top");
  };

  const found: string[] = [];
  for (let first = 0; first < pieces.length; first++) {
    for (let second = first + 1; second < pieces.length; second++) {
      const a = pieces[first];
      const b = pieces[second];
      if (a.element.contains(b.element) || b.element.contains(a.element)) continue;
      const together = a.pinned === b.pinned;
      if (!together) {
        const bar = a.pinned ?? b.pinned;
        if (a.pinned === null || b.pinned === null) {
          if (bar !== null && !inTheWay(bar)) continue;
        } else if (a.pinned.contains(b.pinned) || b.pinned.contains(a.pinned)) {
          continue;
        }
      }
      const hit = a.boxes.some((one) =>
        b.boxes.some((other) => {
          const wide = Math.min(one.right, other.right) - Math.max(one.left, other.left);
          const tall = Math.min(one.bottom, other.bottom) - Math.max(one.top, other.top);
          if (wide <= slack || tall <= slack) return false;
          const oneInTheOther = inside(one, other) || inside(other, one);
          return !(together && (a.control || b.control) && oneInTheOther);
        }),
      );
      if (hit) found.push(`${a.name} and ${b.name} lie over each other`);
    }
  }
  return found;
}

/** Runs in the page: how far the page scrolls sideways, and what reaches past the screen. */
function sideways(): string[] {
  const home = { left: window.scrollX, top: window.scrollY };
  const root = document.scrollingElement ?? document.documentElement;
  window.scrollTo({ left: root.scrollWidth, top: home.top, behavior: "instant" });
  const moved = window.scrollX;
  window.scrollTo({ ...home, behavior: "instant" });
  if (moved === 0) return [];

  const screenWidth = document.documentElement.clientWidth;
  const past: string[] = [];
  for (const element of document.body.querySelectorAll("*")) {
    if (past.length === 3) break;
    const frame = element.getBoundingClientRect();
    if (frame.width === 0 || frame.right + window.scrollX <= screenWidth) continue;
    let clipped = false;
    for (let parent = element.parentElement; parent !== null; parent = parent.parentElement) {
      if (parent !== document.body && getComputedStyle(parent).overflowX !== "visible") {
        clipped = true;
      }
    }
    if (clipped) continue;
    const text = (element.textContent ?? "").replace(/\s+/g, " ").trim().slice(0, 40);
    past.push(`<${element.tagName.toLowerCase()}> "${text}" ends at ${Math.round(frame.right)} px`);
  }
  return [
    `the page scrolls sideways by ${Math.round(moved)} px (the screen is ${screenWidth} px wide): ${past.join("; ")}`,
  ];
}

/** Runs in the page: moves it to its top or to its end, and waits until that is painted. */
async function scrollPageTo(edge: Edge): Promise<void> {
  const root = document.scrollingElement ?? document.documentElement;
  window.scrollTo({ left: 0, top: edge === "top" ? 0 : root.scrollHeight, behavior: "instant" });
  await new Promise((painted) => requestAnimationFrame(() => requestAnimationFrame(painted)));
}

/**
 * Asserts that on the screen `page` shows nothing lies over anything else and that the
 * page does not scroll sideways. The page is left scrolled as it was found.
 */
export async function expectLayoutHolds(page: Page, options: LayoutOptions = {}): Promise<void> {
  const ignore = options.ignore ?? [];
  const home = await page.evaluate(() => ({ left: window.scrollX, top: window.scrollY }));
  const found = new Map<string, Set<Edge>>();
  try {
    for (const edge of ["top", "bottom"] as const) {
      await page.evaluate(scrollPageTo, edge);
      for (const pair of await page.evaluate(overlaps, { edge, ignore, slack: SLACK })) {
        found.set(pair, (found.get(pair) ?? new Set<Edge>()).add(edge));
      }
    }
  } finally {
    await page.evaluate(
      ({ left, top }) => window.scrollTo({ left, top, behavior: "instant" }),
      home,
    );
  }
  const problems = [
    ...(await page.evaluate(sideways)),
    ...[...found].map(([pair, edges]) => {
      const where = [...edges]
        .map((edge) => (edge === "top" ? "its top" : "its end"))
        .join(" and at ");
      return `${pair} (with the page at ${where})`;
    }),
  ];
  const width = page.viewportSize()?.width;
  expect(problems, `the layout of ${page.url()} at ${width} px`).toEqual([]);
}
