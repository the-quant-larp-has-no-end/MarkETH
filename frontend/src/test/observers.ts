/**
 * `IntersectionObserver` and `ResizeObserver` for tests, which jsdom does not have.
 * Nothing intersects until the test says so:
 *
 *     intersect();                                   // every observed element scrolled into view
 *     intersect(screen.getByTestId("load-more"));    // this one did
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
