import { vi } from "vitest";

/**
 * An `EventSource` the test drives, which jsdom does not have. Every stream the app opens
 * is one of these: it knows the address it was opened for, and it hands each event to the
 * listeners of its name.
 *
 *     const streams = installFakeEventSource();
 *     renderWithProviders(<MarketsScreen />, { live: true });
 *     streams.latest().open();
 *     streams.latest().send(readyEvent());
 *     streams.latest().send(marketQuoteEvent({ version: 8 }));   // event: market.quote
 *     streams.latest().fail();          // the stream ended for good; polling starts
 *
 * `send` takes the name of an event from its body, as the API does; `emit` and `emitRaw`
 * name it themselves, for a body the API would not send.
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

  /* ---- What the address says ---- */

  /** The topics the address names, each `topic=` in the order it stands there. */
  get topics(): string[] {
    return this.query.getAll("topic");
  }

  /** The `tab=` of the address: the id of the page load that opened the stream. */
  get tab(): string | null {
    return this.query.get("tab");
  }

  private get query(): URLSearchParams {
    return new URL(this.url, window.location.origin).searchParams;
  }

  /* ---- What the test does to the stream ---- */

  /** The server accepted the stream. */
  open(): void {
    this.readyState = FakeEventSource.OPEN;
    this.onopen?.(new Event("open"));
  }

  /** One event as the API sends it: under the name its body carries as `type`. */
  send<Body extends { type: string }>(event: Body): void {
    this.emit(event.type, event);
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
  /** Every stream the app opened, in the order it opened them. */
  readonly instances: FakeEventSource[];
  /** The stream the app opened last. */
  latest(): FakeEventSource;
  /** The streams the app holds: those it opened and has not closed. */
  held(): FakeEventSource[];
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
    held() {
      return FakeEventSource.instances.filter((stream) => !stream.closed);
    },
  };
}
