"use client";

import { useId, useRef, useState } from "react";

import { ErrorBanner } from "@/components/feedback";
import { Skeleton } from "@/components/ui/skeleton";
import type { ApiError } from "@/lib/api/errors";
import { postChatMessageMutation } from "@/lib/api/mutations";
import type { ChatMessageItem, ChatResponse } from "@/lib/api/schema";
import { useSubmit } from "@/lib/api/submit";
import { cn } from "@/lib/utils";

import { ChatComposer } from "./chat-composer";
import { ChatLog } from "./chat-log";
import { RemoveMessageDialog } from "./remove-message-dialog";
import { useChat } from "./use-chat";

export type ChatPanelProps = {
  /** The id of the market whose live chat this is. */
  marketId: string;
  /**
   * The live stream on screen: `broadcast.id` of the answer about the market's live
   * stream, while its status is LIVE. The panel shows the chat of that live stream and of
   * no other.
   */
  broadcastId: string;
  /**
   * Below 768 px the panel takes the height its column leaves, and never less than
   * 200 px, in place of its 320 px: its viewer watches on a phone (DESIGN.md §13,
   * "Watching on a phone"). The column is a flex column with a height of its own. From
   * 768 px the panel is 360 px tall either way.
   */
  fill?: boolean;
  /**
   * The field of the composer took the focus (true) or gave it away (false), also when
   * it leaves the panel while it has it. While it has it, a phone shows its keyboard.
   */
  onComposerFocus?: (focused: boolean) => void;
  /** Classes of the host for the panel's card, merged after the panel's own. */
  className?: string;
};

/**
 * The live chat of a market (DESIGN.md §13, "Live chat"): a header, the log and the
 * composer. Market detail and the broadcaster's studio mount it while the status of the
 * live stream is LIVE and take it away when it is not.
 *
 * It reads `GET /markets/{market_id}/chat` and follows the event stream (`useChat`). Who
 * may do what is the server's answer: `open` says whether there is a chat, `can_remove`
 * whether the viewer removes. The panel is absent whenever the read answers `NOT_FOUND`.
 *
 * **A chat belongs to one live stream, and none of its messages outlasts it on screen.**
 * The section says which live stream it shows (`broadcastId`). The panel shows the chat
 * of that live stream and of no other, and everything it holds for one live stream (what
 * is typed, the answer to the last message) is let go of when another begins.
 */
export function ChatPanel(props: ChatPanelProps) {
  return <LiveChat key={`${props.marketId}:${props.broadcastId}`} {...props} />;
}

/** What the panel shows: the table of DESIGN.md §13, with content and empty as `open`. */
type View =
  | { state: "loading" }
  | { state: "error"; error: ApiError }
  | { state: "closed" }
  | { state: "open"; chat: ChatResponse };

/** What `useQuery` says about the read of the chat, as far as the panel looks. */
type ChatRead = {
  data: ChatResponse | undefined;
  error: ApiError | null;
  isFetching: boolean;
  /** A read has answered, or failed, since this panel was mounted. */
  isFetchedAfterMount: boolean;
};

/**
 * The state of the panel.
 *
 * What the cache holds when the panel is mounted can be the chat of an earlier live
 * stream, or the closed chat between two of them: the read that the mount begins decides,
 * and until it has answered the panel is loading. Afterwards the rows on screen stay
 * while they are read again, and when reading them again fails.
 *
 * `broadcastId` is the live stream the section shows. The cached chat is shown while it
 * is the chat of that live stream. A read that answers the chat of another one says that
 * the section is behind; the panel waits for it.
 */
function viewOf(read: ChatRead, broadcastId: string, closedBySend: boolean): View {
  const chat = read.data;
  if (closedBySend) return { state: "closed" };
  if (chat !== undefined && chat.broadcast_id === broadcastId) return { state: "open", chat };
  if (read.isFetching && !read.isFetchedAfterMount) return { state: "loading" };
  if (read.error !== null) return { state: "error", error: read.error };
  if (chat !== undefined && !chat.open) return { state: "closed" };
  return { state: "loading" };
}

const NO_MESSAGES: readonly ChatMessageItem[] = [];

/** The bars of the loading state: six, of two widths, each as tall as a line of the log. */
const SKELETON_WIDTHS = ["w-3/4", "w-1/2", "w-3/4", "w-1/2", "w-3/4", "w-1/2"] as const;

/** The panel for one live stream of one market. */
function LiveChat({
  marketId,
  broadcastId,
  fill = false,
  onComposerFocus,
  className,
}: ChatPanelProps) {
  const headingId = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const opener = useRef<HTMLButtonElement | null>(null);
  const { chat: read, follower } = useChat(marketId);
  const post = useSubmit(postChatMessageMutation);
  // `round` gives the confirmation a fresh start for every message it is opened for.
  const [removing, setRemoving] = useState<{ message: ChatMessageItem; round: number } | null>(
    null,
  );
  const [removeOpen, setRemoveOpen] = useState(false);

  // The market is gone for this viewer: nothing stands in the panel's place.
  if (read.error?.code === "NOT_FOUND") return null;

  // A send that is answered `CONFLICT` says that the chat of this live stream is closed.
  const view = viewOf(read, broadcastId, post.error?.code === "CONFLICT");

  return (
    <section
      aria-labelledby={headingId}
      data-slot="chat-panel"
      data-state={view.state}
      className={cn(
        "flex flex-col overflow-hidden rounded-lg border border-border bg-card text-card-foreground",
        fill ? "min-h-50 flex-1 md:h-90 md:min-h-0 md:flex-none" : "h-80 md:h-90",
        className,
      )}
    >
      <div className="flex h-11 shrink-0 items-center px-3">
        <h2 ref={heading} id={headingId} tabIndex={-1} className="type-h3">
          Live chat
        </h2>
      </div>

      {view.state === "loading" && (
        <div
          aria-busy
          data-slot="chat-log-skeleton"
          className="flex min-h-0 flex-1 flex-col gap-1 overflow-hidden px-3 py-2"
        >
          {SKELETON_WIDTHS.map((width, index) => (
            <Skeleton key={index} className={cn("h-5 shrink-0", width)} />
          ))}
        </div>
      )}
      {view.state === "error" && (
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-3">
          <ErrorBanner error={view.error} onRetry={() => void read.refetch()} />
        </div>
      )}
      {(view.state === "open" || view.state === "closed") && (
        <ChatLog
          labelledBy={headingId}
          items={view.state === "open" ? view.chat.items : NO_MESSAGES}
          canRemove={view.state === "open" && view.chat.can_remove}
          closed={view.state === "closed"}
          onRemove={(message, from) => {
            opener.current = from;
            setRemoving((last) => ({ message, round: (last?.round ?? 0) + 1 }));
            setRemoveOpen(true);
          }}
        />
      )}

      {view.state !== "closed" && (
        <ChatComposer
          marketId={marketId}
          post={post}
          disabled={view.state === "loading"}
          onPosted={(message) => follower.put(message)}
          onFocusChange={onComposerFocus}
        />
      )}

      {removing !== null && (
        <RemoveMessageDialog
          key={removing.round}
          marketId={marketId}
          message={removing.message}
          open={removeOpen}
          onClose={() => setRemoveOpen(false)}
          onRemoved={(messageId) => follower.drop(messageId)}
          onCloseAutoFocus={(event) => {
            // The row of a removed message is gone with its button: the heading takes over.
            event.preventDefault();
            const from = opener.current;
            (from !== null && from.isConnected ? from : heading.current)?.focus();
          }}
        />
      )}
    </section>
  );
}
