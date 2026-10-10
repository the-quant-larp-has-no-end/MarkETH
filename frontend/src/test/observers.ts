import { vi } from "vitest";

/**
 * `IntersectionObserver` and `ResizeObserver` for tests, which jsdom does not have.
 * Nothing intersects until the test says so:
 *
 *     intersect();                                   // every observed element scrolled into view
 *     intersect(screen.getByTestId("load-more"));    // this one did
 *
 * A test of something that depends on where an element lies, and not only on whether it
 * is in view, takes `watchScrolling()` instead.
 */

type Callback = (entries: IntersectionObserverEntry[], observer: IntersectionObserver) => void;

const observers = new Set<FakeIntersectionObserver>();

class FakeIntersectionObserver {
  readonly root = null;
  readonly rootMargin = "0px";
  readonly thresholds = [0];
  readonly targets = new Set<Element>();

  constructor(readonly callback: Callback) {
    observers.add(this);
  }

  observe(target: Element): void {
    this.targets.add(target);
  }

  unobserve(target: Element): void {
    this.targets.delete(target);
  }

  disconnect(): void {
    this.targets.clear();
    observers.delete(this);
  }

  takeRecords(): IntersectionObserverEntry[] {
    return [];
  }
}

class FakeResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

/** Reports observed elements as visible: the given one, or all of them. */
export function intersect(target?: Element): void {
  for (const observer of [...observers]) {
    const visible = [...observer.targets].filter((seen) => target === undefined || seen === target);
    if (visible.length === 0) continue;
    const entries = visible.map(
      (seen) =>
        ({ target: seen, isIntersecting: true, intersectionRatio: 1 }) as IntersectionObserverEntry,
    );
    observer.callback(entries, observer as unknown as IntersectionObserver);
  }
}

export function installObservers(): void {
  globalThis.IntersectionObserver =
    FakeIntersectionObserver as unknown as typeof IntersectionObserver;
  globalThis.ResizeObserver = FakeResizeObserver as unknown as typeof ResizeObserver;
}

/** Run before every test (`src/test/setup.ts`). */
export function resetObservers(): void {
  observers.clear();
}

type Watched = {
  callback: IntersectionObserverCallback;
  /** Each observed element, with what was last said of it. */
  targets: Map<Element, boolean | undefined>;
  rootMargin: string;
};

/** The phone of `watchScrolling()`, in CSS pixels: its screen, and what the top bar covers. */
const PHONE = { width: 375, height: 667, topBar: 56 };

function rect(top: number, bottom: number, width: number): DOMRect {
  const edges = { x: 0, y: top, top, left: 0, right: width, bottom, width, height: bottom - top };
  return { ...edges, toJSON: () => edges };
}

/** How far below the screen an observer looks: the third part of its `rootMargin`, in px. */
function reachBelow(rootMargin: string): number {
  const below = /^\S+ \S+ (\d+)px \S+$/.exec(rootMargin)?.[1];
  return below === undefined ? 0 : Number(below);
}

/**
 * An `IntersectionObserver` the test drives, for the rest of the test. The test says where
 * an element lies on a phone whose top bar covers the first 56 px, and every observer of
 * the app hears what a browser would tell it:
 *
 *     const scrolling = watchScrolling();
 *     render(...);
 *     act(() => scrolling.scrolledPast(element));   // it left the screen at the top
 *     act(() => scrolling.inView(element));         // it is on screen
 *     act(() => scrolling.notReached(element));     // it lies below the screen
 *
 * As in a browser, an observer looks at the screen below the top bar and as far beyond
 * its lower edge as its `rootMargin` says, and it is told only when "intersects" changes:
 * an element that was below what the observer looks at and now lies above it has not
 * changed for that observer, however far the page moved.
 *
 * `margins()` are the `rootMargin` of every observer the app made.
 */
export function watchScrolling() {
  const watched = new Set<Watched>();

  class ScrollObserver {
    private readonly entry: Watched;

    constructor(callback: IntersectionObserverCallback, options: IntersectionObserverInit = {}) {
      this.entry = { callback, targets: new Map(), rootMargin: options.rootMargin ?? "0px" };
      watched.add(this.entry);
    }

    observe(target: Element) {
      if (!this.entry.targets.has(target)) this.entry.targets.set(target, undefined);
    }

    unobserve(target: Element) {
      this.entry.targets.delete(target);
    }

    disconnect() {
      this.entry.targets.clear();
      watched.delete(this.entry);
    }

    takeRecords() {
      return [];
    }
  }

  vi.stubGlobal("IntersectionObserver", ScrollObserver);

  const report = (target: Element, top: number, bottom: number) => {
    for (const entry of watched) {
      if (!entry.targets.has(target)) continue;
      const root = rect(PHONE.topBar, PHONE.height + reachBelow(entry.rootMargin), PHONE.width);
      const isIntersecting = bottom > root.top && top < root.bottom;
      if (entry.targets.get(target) === isIntersecting) continue;
      entry.targets.set(target, isIntersecting);

      const record = {
        target,
        isIntersecting,
        intersectionRatio: isIntersecting ? 1 : 0,
        boundingClientRect: rect(top, bottom, PHONE.width - 32),
        rootBounds: root,
      } as unknown as IntersectionObserverEntry;
      entry.callback([record], entry as unknown as IntersectionObserver);
    }
  };

  return {
    /** The element is on screen. */
    inView: (target: Element) => report(target, 300, 348),
    /** The page was scrolled past it: it lies above the top bar. */
    scrolledPast: (target: Element) => report(target, -120, -72),
    /** It has not been reached yet: it lies below the screen. */
    notReached: (target: Element) => report(target, 900, 948),
    margins: () => [...watched].map((entry) => entry.rootMargin),
  };
}
