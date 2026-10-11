import { api } from "./client";
import { ApiError, keepsSubmission, retryAfterSeconds, toApiError } from "./errors";

/**
 * The upload of one piece of a live stream's recording (DESIGN.md §14, "Live video"):
 * `POST /broadcasts/{broadcast_id}/chunks`. It is the one request of the app that is no
 * JSON call. Its body is the recorder's bytes, it is sent about once a second for as long
 * as a live stream is on, and what it comes back with is not an error to show but the
 * next step of the studio's uploader. So it has a function of its own beside `call()`,
 * and no query or mutation: it changes nothing in the cache.
 *
 * It leaves through the same client as every other request, with the same
 * `Accept: application/json`, which is what makes the portal answer a request it has no
 * session for with a 401 instead of a redirect to its sign-in.
 *
 * The portal's 401 is reported here, and the page is not reloaded: a reload would end the
 * camera and with it the live stream. The studio keeps recording and sending while its
 * broadcaster signs in again in another tab, and the piece that was refused goes up with
 * the session that tab brings.
 */

/** One piece on its way up. */
export type BroadcastChunk = {
  /** The live stream that is on: `broadcast.id` of the answer of a start. */
  broadcastId: string;
  /** The part the piece belongs to: an id the studio makes whenever its recorder starts. */
  part: string;
  /** The place of the piece in its part, counted from 0. A piece sent again keeps it. */
  seq: number;
  /** The recorder's bytes, as they are. A piece sent again is the same bytes. */
  body: Blob;
  /** `broadcast.upload_timeout_ms` of `GET /config`: how long the request may take. */
  timeoutMs: number;
  /** Ends the request from outside: the studio begins another part, or stops. */
  signal?: AbortSignal;
};

/**
 * What became of a piece, as the step the uploader takes next.
 *
 * - `ok`: the piece is taken. `nextSeq` is the place of the piece to send next in this
 *   part.
 * - `restart`: the piece is not taken and its part is over. The uploader drops what it
 *   holds, starts its recorder again and begins another part at place 0.
 * - `repeat`: nobody can say whether the piece was taken: no answer in time, an error of
 *   the server, a 429, or the portal's 401. The same bytes go up again under the same
 *   place, after `broadcast.upload_retry_ms` of `GET /config`, or after
 *   `retryAfterSeconds` where the answer names a wait. `signedOut` says that the portal
 *   has no session for the broadcaster: the studio shows its sign-in banner until a piece
 *   is answered again.
 * - `stop`: the API refused the piece, and would refuse it again: the live stream has
 *   ended, or it is not the caller's to send to. The uploader stops for good and the
 *   studio shows `error.message`, the API's sentence.
 *
 * A request the caller ended through `signal` comes back as `repeat`, like any request
 * without an answer. The caller that ended it knows, and does not act on it.
 */
export type BroadcastChunkAnswer =
  | { outcome: "ok"; nextSeq: number }
  | { outcome: "restart" }
  | {
      outcome: "repeat";
      error: ApiError;
      /** The portal answered 401: its sign-in has ended. The page stays as it is. */
      signedOut: boolean;
      /** The wait a `RATE_LIMITED` answer asks for, and null for every other answer. */
      retryAfterSeconds: number | null;
    }
  | { outcome: "stop"; error: ApiError };

/**
 * A failed upload as a step. The rule is that of every write that is sent again unchanged
 * (`keepsSubmission`, DESIGN.md §14): only a 4xx other than 429 says that the API refuses
 * it. A 5xx, a 429, the portal's 401 and no answer say nothing about the piece.
 */
function afterFailure(error: ApiError): BroadcastChunkAnswer {
  if (!keepsSubmission(error)) return { outcome: "stop", error };
  return {
    outcome: "repeat",
    error,
    signedOut: error.kind === "portal",
    retryAfterSeconds: retryAfterSeconds(error),
  };
}

/**
 * Sends one piece and says what became of it. It never throws and never reloads the page.
 *
 *     const answer = await sendBroadcastChunk({ broadcastId, part, seq, body, timeoutMs });
 *     if (answer.outcome === "ok") seq = answer.nextSeq;
 */
export async function sendBroadcastChunk(chunk: BroadcastChunk): Promise<BroadcastChunkAnswer> {
  const { broadcastId, part, seq, body, timeoutMs, signal } = chunk;
  // One signal for both ways a request ends early: its time is up, or the caller ended it.
  const request = new AbortController();
  const end = () => request.abort();
  const timer = window.setTimeout(end, timeoutMs);
  if (signal?.aborted) end();
  signal?.addEventListener("abort", end);

  try {
    const answer = await api().POST("/api/broadcasts/{broadcast_id}/chunks", {
      params: { path: { broadcast_id: broadcastId }, query: { part, seq } },
      // The contract types a binary body as text. It goes out as the bytes it is.
      body: body as unknown as string,
      bodySerializer: (bytes) => bytes,
      headers: { "Content-Type": "application/octet-stream" },
      signal: request.signal,
    });
    if (!answer.response.ok) {
      return afterFailure(toApiError(answer.response.status, answer.error));
    }
    // A 2xx that is not the answer of the API is somebody else's page.
    const taken = answer.data;
    if (typeof taken?.restart !== "boolean" || typeof taken.next_seq !== "number") {
      return afterFailure(ApiError.unavailable(0));
    }
    return taken.restart ? { outcome: "restart" } : { outcome: "ok", nextSeq: taken.next_seq };
  } catch (cause) {
    // A 2xx whose body is not JSON is somebody else's page; anything else never arrived.
    return afterFailure(
      cause instanceof SyntaxError ? ApiError.unavailable(0, cause) : ApiError.network(cause),
    );
  } finally {
    window.clearTimeout(timer);
    signal?.removeEventListener("abort", end);
  }
}
