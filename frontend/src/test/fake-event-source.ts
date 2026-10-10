import { vi } from "vitest";

/**
 * An `EventSource` the test drives, which jsdom does not have.
 *
 *     const streams = installFakeEventSource();
 *     render(...);
 *     streams.latest().open();
 *     streams.latest().emit("quote", quoteEvent({ version: 8 }));
 *     streams.latest().fail();          // the stream ended for good; polling starts
 */
export class FakeEventSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  static instances: FakeEventSource[] = [];

  readonly url: string;
  readyState: number = FakeEventSource.CONNECTING;
  onopen: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  /** True once the app closed the stream. */
  closed = false;
  private readonly listeners = new Map<string, Set<(event: Event) => void>>();

  constructor(url: string | URL) {
    this.url = String(url);
    FakeEventSource.instances.push(this);
  }

  addEventListener(name: string, listener: (event: Event) => void): void {
    let set = this.listeners.get(name);
    if (set === undefined) {
      set = new Set();
      this.listeners.set(name, set);
    }
    set.add(listener);
  }

  removeEventListener(name: string, listener: (event: Event) => void): void {
    this.listeners.get(name)?.delete(listener);
  }

  close(): void {
    this.closed = true;
    this.readyState = FakeEventSource.CLOSED;
  }

  /* ---- What the test does to the stream ---- */

  /** The server accepted the stream. */
  open(): void {
    this.readyState = FakeEventSource.OPEN;
    this.onopen?.(new Event("open"));
  }

  /** One event, with `data` as its JSON. */
  emit(name: string, data: unknown): void {
    this.emitRaw(name, JSON.stringify(data));
  }

  /** One event whose `data:` is this text, JSON or not. */
  emitRaw(name: string, data: string): void {
    const event = new MessageEvent(name, { data });
    for (const listener of this.listeners.get(name) ?? []) listener(event);
  }

  /**
   * The stream failed. `closed: true` (the default) is an HTTP error, which ends it for
   * good; `closed: false` is a dropped connection, which the browser retries by itself.
   */
  fail({ closed = true }: { closed?: boolean } = {}): void {
    this.readyState = closed ? FakeEventSource.CLOSED : FakeEventSource.CONNECTING;
    this.onerror?.(new Event("error"));
  }
}

/** Puts the fake in place of `EventSource` for the rest of the test. */
export function installFakeEventSource(): {
  readonly instances: FakeEventSource[];
  /** The stream the app opened last. */
  latest(): FakeEventSource;
} {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
  return {
    get instances() {
      return FakeEventSource.instances;
    },
    latest() {
      const stream = FakeEventSource.instances.at(-1);
      if (stream === undefined) throw new Error("The app has not opened an event stream.");
      return stream;
    },
  };
}
