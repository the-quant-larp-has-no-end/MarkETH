"use client";

import { Trash2Icon } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import type { ChatMessageItem } from "@/lib/api/schema";
import { cn } from "@/lib/utils";

/** The two sentences that stand in the log in place of its rows. */
const EMPTY = "No messages yet.";
const CLOSED = "The live chat is closed.";

/**
 * One line of a chat (DESIGN.md §13, "Live chat"): the username, the badge "VIS" where
 * the server says that an admin of VIS wrote it, and the text. It is one line that
 * wraps, a string without a space included, and it clips what would leave it.
 *
 * What a user wrote is text: it is shown character for character, as a text node.
 * Nothing in it is markup, no part of it becomes a link, and no address is built from it.
 */
export function ChatLine({ message, className }: { message: ChatMessageItem; className?: string }) {
  return (
    <p data-slot="chat-line" className={cn("min-w-0 type-body-sm wrap-anywhere", className)}>
      <span className="type-caption text-muted-foreground">{message.username}</span>
      {message.from_vis && (
        <>
          {" "}
          <span
            data-slot="vis-badge"
            className="rounded-sm bg-raised px-1 type-caption text-foreground"
          >
            VIS
          </span>
        </>
      )}{" "}
      {message.text}
    </p>
  );
}

/**
 * The log of the live chat (DESIGN.md §13): the rows in the order they arrive, or the
 * sentence of an empty or a closed chat in their place. It scrolls inside the panel, and
 * the page behind does not move when it reaches an end.
 *
 * It stays at its end while the reader is there, so a message that arrives is in sight,
 * also when the log or what it holds changes its height by itself (a keyboard that slides
 * up, rows that wrap anew). Once the end is out of sight, what arrives does not move the
 * view, and "Latest messages" takes the log to its end. The button stands in a row of its
 * own under the rows: nothing lies over a message.
 *
 * It is a `log` for a screen reader: a message that arrives is told politely, and what
 * stands in it when it is mounted is not read out.
 */
export function ChatLog({
  labelledBy,
  items,
  canRemove,
  closed,
  onRemove,
}: {
  /** The id of the panel's heading: the log carries the same name. */
  labelledBy: string;
  items: readonly ChatMessageItem[];
  /** `can_remove` of the chat: each row ends in its remove button. */
  canRemove: boolean;
  /** The chat is closed: its sentence stands in place of the rows. */
  closed: boolean;
  /** The remove button of a row was tapped. `opener` takes the focus back afterwards. */
  onRemove: (message: ChatMessageItem, opener: HTMLButtonElement) => void;
}) {
  const log = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const end = useRef<HTMLDivElement>(null);
  /**
   * Whether the end of the log is in sight. The browser says so, through the observer of
   * the mark at the end, and nothing else writes it: the effects read `reading`, and the
   * button "Latest messages" shows by `atEnd`.
   */
  const reading = useRef(true);
  const [atEnd, setAtEnd] = useState(true);

  useEffect(() => {
    const [frame, mark] = [log.current, end.current];
    if (frame === null || mark === null) return;
    const observer = new IntersectionObserver(
      (entries) => {
        const seen = entries.at(-1)?.isIntersecting ?? true;
        reading.current = seen;
        setAtEnd(seen);
      },
      { root: frame },
    );
    observer.observe(mark);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const [frame, held] = [log.current, content.current];
    if (frame === null || held === null) return;
    const observer = new ResizeObserver(() => {
      if (reading.current) frame.scrollTop = frame.scrollHeight;
    });
    observer.observe(frame);
    observer.observe(held);
    return () => observer.disconnect();
  }, []);

  useLayoutEffect(() => {
    const frame = log.current;
    if (frame !== null && reading.current && items.length > 0) frame.scrollTop = frame.scrollHeight;
  }, [items]);

  const toLatest = () => {
    const frame = log.current;
    if (frame === null) return;
    // Whether the end is in sight is the browser's to say, here as everywhere: the button
    // goes when it is. The log keeps the place of whoever used the keyboard.
    frame.scrollTop = frame.scrollHeight;
    frame.focus({ preventScroll: true });
  };

  const sentence = closed ? CLOSED : items.length === 0 ? EMPTY : null;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div
        ref={log}
        role="log"
        aria-labelledby={labelledBy}
        tabIndex={0}
        data-slot="chat-log"
        className="min-h-0 flex-1 overflow-y-auto overscroll-contain focus-visible:-outline-offset-2"
      >
        <div ref={content} className="flex min-h-full flex-col">
          {sentence !== null ? (
            <p className="m-auto px-3 py-2 text-center type-body-sm text-muted-foreground">
              {sentence}
            </p>
          ) : (
            <ul className="flex flex-col gap-1 py-2">
              {items.map((message) => (
                <li
                  key={message.id}
                  className={cn(
                    "flex items-start gap-1 overflow-hidden pl-3",
                    canRemove ? "min-h-11 pr-1" : "pr-3",
                  )}
                >
                  <ChatLine message={message} className={cn("flex-1", canRemove && "py-3")} />
                  {canRemove && (
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Remove the message from ${message.username}`}
                      onClick={(event) => onRemove(message, event.currentTarget)}
                    >
                      <Trash2Icon aria-hidden />
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
          <div ref={end} aria-hidden data-slot="chat-log-end" className="h-px shrink-0" />
        </div>
      </div>
      {!atEnd && sentence === null && (
        <div className="flex shrink-0 justify-center px-3 pb-1">
          <Button variant="secondary" size="compact" width="fit" onClick={toLatest}>
            Latest messages
          </Button>
        </div>
      )}
    </div>
  );
}
