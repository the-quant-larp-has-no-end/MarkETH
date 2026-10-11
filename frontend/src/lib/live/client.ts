import { newUuid } from "../uuid";
import { LIVE_EVENT_NAMES, type LiveEvent, type LiveEventName } from "./events";
import type { LiveTopic } from "./topics";

/** The event stream: the one address of the API that is opened, not called. */
const STREAM = "/api/stream";

/**
 * A change of the wanted topics is acted on this long after the last one: a navigation
 * takes a topic away and adds another in the same moment, and that is one exchange of the
 * stream, not two.
 */
const RESUBSCRIBE_MS = 50;

/** A stream that brought nothing for this many heartbeats is a dead path. */
const SILENT_HEARTBEATS = 2;

let tab: string | null = null;

/**
 * The id of this page load, made at its first stream. Every stream of the page names it
 * as `tab`, and the API ends the stream it still holds under that id when another one
 * opens: a stream the browser left behind gives its place back.
 */
function tabOfThisPage(): string {
  tab ??= newUuid();
  return tab;
}

export type LiveClientOptions = {
  /** `live_heartbeat_seconds` of `/config`: how often an open stream says that it is alive. */
  heartbeatSeconds: number;
  /** `live_poll_fallback_seconds` of `/config`: the pace of the polls while there is no stream. */
  pollSeconds: number;
  /** Every event that arrives, `ready` and `beat` among them. */
  onEvent: (event: LiveEvent) => void;
  /**
   * The polling fallback. Called while the stream is lost: the place to ask the API for
   * what the stream would have brought.
   */
  onPoll: () => void;
};

/**
 * The tab's one event stream (DESIGN.md §14, "Live data"), without React.
 *
 * - **One stream while the page is visible, none while it is hidden.** Its address names
 *   the tab and the topics that are wanted, in sorted order. When the wanted topics
 *   change, the stream is exchanged for one that names the changed set.
 * - **`ready` makes it live.** `live` is true from `ready` until a loss, and keeps its
 *   value while the stream is exchanged for changed topics. No event is sent a second
 *   time, so whoever listens reads once at every `ready` what the topics cover.
 * - **A loss costs speed, never an error on screen.** The stream is lost when it answers
 *   an error, when it ends and when nothing is heard for two heartbeats, and a browser
 *   without `EventSource` never has one. From a loss on `onPoll` is called: first after a
 *   random part of the poll interval, so that the tabs of a busy moment do not ask in the
 *   same instant, then every interval, and never sooner than an interval after the call
 *   before. Each poll tries the stream again, unless the browser is reconnecting by
 *   itself. The first `ready` ends the polling.
 * - **A hidden page** holds no stream and no timer. Shown again, it opens its stream.
 */
export class LiveClient {
  private readonly options: LiveClientOptions;
  /** How many followers each wanted topic has. */
  private readonly wanted = new Map<LiveTopic, number>();
  private readonly listeners = new Set<() => void>();

  private started = false;
  /** Started, and the page is visible. */
  private awake = false;
  private isLive = false;

  /** The stream at hand: open, opening, or being retried by the browser. */
  private source: EventSource | null = null;
  /** The topics the stream at hand was opened with. */
  private sourceTopics = "";
  /** When the stream at hand last said anything, or was opened. */
  private heardAt = 0;
  private polledAt = Number.NEGATIVE_INFINITY;

  private resubscribeTimer: number | null = null;
  private watchdogTimer: number | null = null;
  private pollTimer: number | null = null;

  constructor(options: LiveClientOptions) {
    this.options = options;
  }

  /** True from `ready` until a loss. */
  get live(): boolean {
    return this.isLive;
  }

  /** The topics that are wanted, sorted: what the mounted screens name together. */
  get topics(): readonly LiveTopic[] {
    return [...this.wanted.keys()].sort();
  }

  /** Calls `listener` whenever `live` changes. Returns the function that ends it. */
  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  /**
   * Wants a topic until the returned function is called. A topic that several followers
   * name is wanted once, for as long as one of them holds it.
   */
  follow(topic: LiveTopic): () => void {
    const followers = this.wanted.get(topic) ?? 0;
    this.wanted.set(topic, followers + 1);
    if (followers === 0) this.topicsChanged();

    let held = true;
    return () => {
      if (!held) return;
      held = false;
      const left = (this.wanted.get(topic) ?? 1) - 1;
      if (left > 0) {
        this.wanted.set(topic, left);
      } else {
        this.wanted.delete(topic);
        this.topicsChanged();
      }
    };
  }

  /** Opens the stream if the page is visible, and follows the page's visibility from here on. */
  start(): void {
    if (this.started) return;
    this.started = true;
    document.addEventListener("visibilitychange", this.onVisibility);
    window.addEventListener("online", this.onOnline);
    if (document.visibilityState !== "hidden") this.wake();
  }

  /** Closes the stream and stops every timer. */
  stop(): void {
    if (!this.started) return;
    this.started = false;
    document.removeEventListener("visibilitychange", this.onVisibility);
    window.removeEventListener("online", this.onOnline);
    this.rest();
  }

  /* ---- The page ---- */

  private onVisibility = (): void => {
    if (document.visibilityState === "hidden") this.rest();
    else this.wake();
  };

  /** The connection is back: while the stream is lost, the next poll does not wait out its interval. */
  private onOnline = (): void => {
    if (this.pollTimer === null) return;
    window.clearTimeout(this.pollTimer);
    this.pollTimer = window.setTimeout(this.poll, Math.max(0, this.nextPollAt() - Date.now()));
  };

  private wake(): void {
    if (this.awake) return;
    this.awake = true;
    this.watchdogTimer = window.setInterval(this.watch, this.options.heartbeatSeconds * 1000);
    this.open();
  }

  private rest(): void {
    this.awake = false;
    this.drop();
    for (const timer of [this.resubscribeTimer, this.pollTimer]) {
      if (timer !== null) window.clearTimeout(timer);
    }
    if (this.watchdogTimer !== null) window.clearInterval(this.watchdogTimer);
    this.resubscribeTimer = null;
    this.pollTimer = null;
    this.watchdogTimer = null;
    this.setLive(false);
  }

  /* ---- The stream ---- */

  private address(): string {
    const query = new URLSearchParams({ tab: tabOfThisPage() });
    for (const topic of this.topics) query.append("topic", topic);
    return `${STREAM}?${query.toString()}`;
  }

  private open(): void {
    if (typeof EventSource === "undefined") {
      // Nothing will ever open: the tab polls.
      this.lose();
      return;
    }
    const source = new EventSource(this.address());
    this.source = source;
    this.sourceTopics = this.topics.join("\n");
    this.heardAt = Date.now();

    source.onopen = () => {
      if (source === this.source) this.heardAt = Date.now();
    };
    source.onerror = () => {
      if (source !== this.source) return;
      // An HTTP error ends a stream for good: it is let go and tried again at the polls.
      // A dropped connection is the browser's to retry, and the stream stays at hand.
      if (source.readyState === EventSource.CLOSED) this.drop();
      this.lose();
    };
    for (const name of LIVE_EVENT_NAMES) {
      source.addEventListener(name, (message) => {
        this.receive(source, name, (message as MessageEvent<unknown>).data);
      });
    }
  }

  /** Lets go of the stream at hand. */
  private drop(): void {
    this.source?.close();
    this.source = null;
  }

  private receive(source: EventSource, name: LiveEventName, data: unknown): void {
    if (source !== this.source) return;
    this.heardAt = Date.now();

    const event = bodyOf(name, data);
    // A frame that is not the JSON of its event carries nothing to show.
    if (event === null) return;

    if (event.type === "ready") {
      this.stopPolling();
      this.setLive(true);
    }
    this.options.onEvent(event);
  }

  /** The stream is lost: nothing on screen says so, and the tab asks instead. */
  private lose(): void {
    this.setLive(false);
    if (this.pollTimer !== null) return;
    // The first poll comes after a random part of the interval, and never sooner than an
    // interval after the poll before: a stream that opens and fails at once asks no faster.
    const every = this.options.pollSeconds * 1000;
    const wait = Math.max(Math.random() * every, this.nextPollAt() - Date.now());
    this.pollTimer = window.setTimeout(this.poll, wait);
  }

  private nextPollAt(): number {
    return this.polledAt + this.options.pollSeconds * 1000;
  }

  private poll = (): void => {
    this.pollTimer = window.setTimeout(this.poll, this.options.pollSeconds * 1000);
    this.polledAt = Date.now();
    // A stream at hand is the browser's to bring back; without one the tab tries again.
    if (this.source === null) this.open();
    this.options.onPoll();
  };

  private stopPolling(): void {
    if (this.pollTimer === null) return;
    window.clearTimeout(this.pollTimer);
    this.pollTimer = null;
  }

  /**
   * Looks every heartbeat. A live stream that brought nothing for two of them is a dead
   * path: it is closed and opened again at once. A stream that was opened and never said
   * anything in that time is lost like one that answered an error.
   *
   * While the tab polls, the polls see to the stream. A stream that is open and silent is
   * let go here, so that the next poll tries again; one that is still connecting is the
   * browser's to bring back.
   */
  private watch = (): void => {
    const source = this.source;
    if (source === null) return;
    const silentFor = Date.now() - this.heardAt;
    if (silentFor < SILENT_HEARTBEATS * this.options.heartbeatSeconds * 1000) return;

    if (this.pollTimer !== null) {
      if (source.readyState === EventSource.OPEN) this.drop();
      return;
    }
    this.drop();
    if (this.isLive) {
      this.setLive(false);
      this.open();
    } else {
      this.lose();
    }
  };

  /* ---- The topics ---- */

  private topicsChanged(): void {
    // A hidden page holds no timer: the stream it opens when it is shown names what is
    // wanted then.
    if (!this.awake) return;
    if (this.resubscribeTimer !== null) window.clearTimeout(this.resubscribeTimer);
    this.resubscribeTimer = window.setTimeout(this.resubscribe, RESUBSCRIBE_MS);
  }

  /**
   * Exchanges the stream at hand for one that names the wanted topics, if those are others
   * than it was opened with. Without a stream at hand there is nothing to exchange: the
   * next one that is opened names what is wanted then.
   */
  private resubscribe = (): void => {
    this.resubscribeTimer = null;
    if (this.source === null || this.topics.join("\n") === this.sourceTopics) return;
    this.drop();
    this.open();
  };

  private setLive(live: boolean): void {
    if (live === this.isLive) return;
    this.isLive = live;
    for (const listener of [...this.listeners]) listener();
  }
}

/** The body of an event that travelled under `name`, or null for a frame that is none. */
function bodyOf(name: LiveEventName, data: unknown): LiveEvent | null {
  if (typeof data !== "string") return null;
  let body: unknown;
  try {
    body = JSON.parse(data);
  } catch {
    return null;
  }
  if (typeof body !== "object" || body === null) return null;
  return (body as { type?: unknown }).type === name ? (body as LiveEvent) : null;
}
