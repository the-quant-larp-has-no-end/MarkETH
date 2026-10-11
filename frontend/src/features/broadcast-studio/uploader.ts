import { type BroadcastChunkAnswer, sendBroadcastChunk } from "@/lib/api/broadcast-upload";
import type { ApiError } from "@/lib/api/errors";
import type { BroadcastConfig } from "@/lib/api/schema";
import { newUuid } from "@/lib/uuid";

/** Why another part begins. */
export type PartReason =
  /** The server did not take a piece: the part is over for it. */
  | "restart"
  /** More waits to be sent than may wait: the connection does not carry the stream. */
  | "backlog"
  /** The recorder handed nothing over for too long. */
  | "gap"
  /** The studio asks for one: the picture changed its shape. */
  | "asked";

/** What the studio says about its connection while it is live. */
export type UploadNotice = {
  /** More than half of what may wait is waiting. */
  slow: boolean;
  /** The portal answered an upload with its 401: its sign-in has ended. */
  signedOut: boolean;
};

export type UploaderOptions = {
  /** The live stream the pieces go to: `broadcast.id` of the answer of the start. */
  broadcastId: string;
  /** `broadcast` of `GET /config`: every size and every pace of the upload. */
  config: BroadcastConfig;
  /**
   * Another part has begun: the recorder is started again on the same camera, because the
   * first piece of a run is the only one a decoder can begin with.
   */
  onPart: (reason: PartReason) => void;
  /** The upload had to start over too often: the studio pauses. Nothing more is sent. */
  onPause: () => void;
  /** The API refuses the pieces, and would refuse them again. Nothing more is sent. */
  onStop: (error: ApiError) => void;
  /** What the studio says about its connection changed. */
  onNotice: (notice: UploadNotice) => void;
};

/** The request that is out, with the body it keeps for every time it is sent. */
type Out = {
  part: string;
  seq: number;
  body: Blob;
  /** Ends the request from here: another part begins, or the upload stops. */
  abort: AbortController;
  /** Ends the wait before the same body is sent again. */
  wake: (() => void) | null;
};

/** How many parts may begin within `restart_window_ms` before the studio pauses. */
const PARTS_IN_WINDOW = 3;

/**
 * The upload of a live stream (DESIGN.md §14, "Live video"): the pieces of the recorder,
 * sent to `POST /broadcasts/{broadcast_id}/chunks` one request at a time.
 *
 * - **A part** is the pieces of one run of the recorder, under an id made here. Inside a
 *   part every request has a place (`seq`), counted from 0 and taken from the answer.
 * - **One request is out at a time.** Its body is the pieces at the head of the queue, as
 *   many as fit `chunk_max_bytes`; a piece larger than that is cut, and its rest stays at
 *   the head. A recorder hands over megabytes at once after a sleep, and a recorder of MP4
 *   with its first piece.
 * - **A body is kept as it is** until it is answered: every time it is sent again it is
 *   the same bytes under the same place.
 * - **The answer decides:** taken, the next body goes up. Not taken, another part begins.
 *   Refused by the API, the upload stops for good. Anything else says nothing about the
 *   piece, and the same body goes up again after `upload_retry_ms`, or after the wait a
 *   `RATE_LIMITED` names. The portal's 401 is one of those, and the studio says so.
 * - **Nothing is skipped inside a part.** What cannot be sent waits. When more waits than
 *   `upload_backlog_bytes`, it is dropped and another part begins.
 * - **A recorder that falls silent is started again.** When no piece arrived for
 *   `restart_window_ms`, another part begins: a recorder whose picture stopped hands
 *   nothing over and tells nobody.
 * - **Three parts within `restart_window_ms` pause the studio** in place of a fourth:
 *   something else sends for this live stream, or the connection cannot carry it. Counted
 *   are the first part and the parts the upload itself had to begin, not the ones after a
 *   silent recorder or a picture that changed its shape.
 *
 * It holds no React and no camera: `onPart` is where the studio starts its recorder again.
 */
export class Uploader {
  private readonly options: UploaderOptions;
  private part = newUuid();
  private seq = 0;
  /** The pieces that wait, oldest first. */
  private queue: Blob[] = [];
  /** The bytes that wait: the queue and the body that is out. */
  private waiting = 0;
  private out: Out | null = null;
  private stopped = false;
  /** When the parts that count against the window began. */
  private begun: number[] = [Date.now()];
  private notice: UploadNotice = { slow: false, signedOut: false };
  private silence: number | null = null;

  constructor(options: UploaderOptions) {
    this.options = options;
    this.listen();
  }

  /** A piece of the recorder that runs for the part at hand. An empty one is dropped. */
  push(piece: Blob): void {
    if (this.stopped || piece.size === 0) return;
    this.listen();
    this.queue.push(piece);
    this.waiting += piece.size;
    if (this.waiting > this.options.config.upload_backlog_bytes) {
      this.anotherPart("backlog");
      return;
    }
    this.say({});
    this.pump();
  }

  /**
   * Ends the part at hand and begins another: the request that is out is let go, what
   * waits is dropped, and `onPart` starts the recorder again. Three parts within the
   * window pause the studio instead.
   */
  anotherPart(reason: PartReason): void {
    if (this.stopped) return;
    this.letGo();

    if (reason === "restart" || reason === "backlog") {
      const now = Date.now();
      const recent = this.begun.filter((at) => now - at < this.options.config.restart_window_ms);
      if (recent.length >= PARTS_IN_WINDOW) {
        this.stop();
        this.options.onPause();
        return;
      }
      this.begun = [...recent, now];
    }

    this.part = newUuid();
    this.seq = 0;
    this.listen();
    this.say({});
    this.options.onPart(reason);
  }

  /** Stops for good: nothing more is sent, and no answer is acted on. */
  stop(): void {
    this.stopped = true;
    this.letGo();
    if (this.silence !== null) window.clearTimeout(this.silence);
    this.silence = null;
  }

  /* ---- One request at a time ---- */

  private pump(): void {
    if (this.out !== null || this.stopped || this.queue.length === 0) return;
    const request: Out = {
      part: this.part,
      seq: this.seq,
      body: this.takeBody(),
      abort: new AbortController(),
      wake: null,
    };
    this.out = request;
    void this.send(request);
  }

  /** The pieces at the head of the queue, as many as one request may carry. */
  private takeBody(): Blob {
    const most = this.options.config.chunk_max_bytes;
    const pieces: Blob[] = [];
    let bytes = 0;
    while (this.queue.length > 0) {
      const head = this.queue[0]!;
      if (bytes + head.size <= most) {
        pieces.push(head);
        bytes += head.size;
        this.queue.shift();
      } else if (bytes === 0) {
        // Larger than a request: cut, and its rest stays at the head.
        pieces.push(head.slice(0, most));
        this.queue[0] = head.slice(most);
        break;
      } else {
        break;
      }
    }
    return pieces.length === 1 ? pieces[0]! : new Blob(pieces);
  }

  /** Sends one body until it is answered, the same bytes under the same place each time. */
  private async send(request: Out): Promise<void> {
    const { broadcastId, config } = this.options;
    for (;;) {
      const answer = await sendBroadcastChunk({
        broadcastId,
        part: request.part,
        seq: request.seq,
        body: request.body,
        timeoutMs: config.upload_timeout_ms,
        signal: request.abort.signal,
      });
      // An answer to a request that was let go belongs to nobody, a late "taken" included.
      if (this.out !== request) return;
      this.say({ signedOut: signedOutAfter(answer, this.notice.signedOut) });

      if (answer.outcome === "ok") {
        this.waiting -= request.body.size;
        this.seq = answer.nextSeq;
        this.out = null;
        this.say({});
        this.pump();
        return;
      }
      if (answer.outcome === "restart") {
        this.anotherPart("restart");
        return;
      }
      if (answer.outcome === "stop") {
        this.stop();
        this.options.onStop(answer.error);
        return;
      }

      const wait =
        answer.retryAfterSeconds === null
          ? config.upload_retry_ms
          : answer.retryAfterSeconds * 1000;
      await new Promise<void>((resolve) => {
        const timer = window.setTimeout(resolve, wait);
        request.wake = () => {
          window.clearTimeout(timer);
          resolve();
        };
      });
      request.wake = null;
      if (this.out !== request) return;
    }
  }

  /** Lets the request that is out go, and drops what waits. */
  private letGo(): void {
    const request = this.out;
    this.out = null;
    this.queue = [];
    this.waiting = 0;
    if (request === null) return;
    request.abort.abort();
    request.wake?.();
  }

  /* ---- What the studio is told ---- */

  /** Counts the silence of the recorder from now. */
  private listen(): void {
    if (this.silence !== null) window.clearTimeout(this.silence);
    this.silence = window.setTimeout(
      () => this.anotherPart("gap"),
      this.options.config.restart_window_ms,
    );
  }

  private say(change: Partial<UploadNotice>): void {
    const next: UploadNotice = {
      signedOut: change.signedOut ?? this.notice.signedOut,
      slow: this.waiting * 2 > this.options.config.upload_backlog_bytes,
    };
    if (next.slow === this.notice.slow && next.signedOut === this.notice.signedOut) return;
    this.notice = next;
    this.options.onNotice(next);
  }
}

/**
 * Whether the portal's sign-in is known to have ended, after this answer. Its 401 says
 * so, and every answer that came through the portal says the opposite. A request without
 * an answer says nothing either way.
 */
function signedOutAfter(answer: BroadcastChunkAnswer, before: boolean): boolean {
  if (answer.outcome !== "repeat") return false;
  if (answer.signedOut) return true;
  return answer.error.kind === "network" ? before : false;
}
