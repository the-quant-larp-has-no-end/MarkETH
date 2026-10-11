"use client";

import { SendHorizontalIcon } from "lucide-react";
import { type FormEvent, useEffect, useEffectEvent, useId, useRef, useState } from "react";

import { InlineError, RetryCountdown } from "@/components/feedback";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { messageOf, placeError } from "@/lib/api/errors";
import type { ChatMessageItem } from "@/lib/api/schema";
import { useConfig } from "@/lib/api/session";
import type { Submit } from "@/lib/api/submit";
import { useRetryAfter } from "@/lib/hooks";

/** The one field of a message, as the API names it in a refusal. */
const FIELDS = ["text"] as const;

/** `POST /markets/{market_id}/chat`, as the panel sends it through `useSubmit`. */
export type ChatPost = Submit<ChatMessageItem, { marketId: string; text: string }>;

/**
 * The composer of the live chat (DESIGN.md §13): a one-line field and its send button,
 * which are one form, so Enter sends. Under the field stand what the API said to the
 * last message and how much of `chat_message_max_length` is typed.
 *
 * - The text goes out with the characters that were typed: the API cleans and measures it.
 * - From the tap until the answer nothing is sent a second time and the field holds what
 *   was sent. It is empty once the answer says the message was taken, and keeps its text
 *   after every other answer and after none.
 * - A refusal shows the API's sentence under the field. After `RATE_LIMITED` sending is
 *   disabled for the time the API names, with the countdown; the refusal stays for that
 *   time, and what is typed afterwards takes it away.
 *
 * A send that is answered `CONFLICT` closes the chat: the panel then shows its closed
 * state and no composer.
 */
export function ChatComposer({
  marketId,
  post,
  disabled = false,
  onPosted,
  onFocusChange,
}: {
  marketId: string;
  post: ChatPost;
  /** The chat is still being read: nothing can be typed or sent. */
  disabled?: boolean;
  /** The answer of a post: the message as it is stored. */
  onPosted: (message: ChatMessageItem) => void;
  /** The field took the focus (true) or gave it away (false). */
  onFocusChange?: (focused: boolean) => void;
}) {
  const { chat_message_max_length: maxLength } = useConfig();
  const [text, setText] = useState("");
  const field = useRef<HTMLInputElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const focused = useRef(false);
  const errorId = useId();

  const error = post.error;
  const wait = useRetryAfter(error);
  const placed = error === null ? null : placeError(error, FIELDS);
  const sentence = error === null ? null : (placed?.fields.text ?? messageOf(error));
  const blocked = disabled || post.isPending || wait > 0 || text === "";

  const focusChanged = (now: boolean) => {
    focused.current = now;
    onFocusChange?.(now);
  };
  // A composer that leaves the panel with the focus in its field says so by no blur.
  const left = useEffectEvent(() => {
    if (focused.current) onFocusChange?.(false);
  });
  useEffect(() => () => left(), []);

  const send = (event: FormEvent) => {
    event.preventDefault();
    if (blocked) return;
    post.mutate(
      { marketId, text },
      {
        onSuccess: (message) => {
          setText("");
          onPosted(message);
          // The button gave the focus up while it waited: whoever sent with it writes on.
          if (document.activeElement === button.current) field.current?.focus();
        },
      },
    );
  };

  return (
    <form
      noValidate
      onSubmit={send}
      data-slot="chat-composer"
      className="flex shrink-0 flex-col gap-1 border-t border-border p-2"
    >
      <div className="flex gap-2">
        <Input
          ref={field}
          type="text"
          name="text"
          aria-label="Message"
          placeholder="Write a message"
          autoComplete="off"
          enterKeyHint="send"
          maxLength={maxLength}
          value={text}
          disabled={disabled}
          readOnly={post.isPending}
          aria-invalid={placed?.fields.text !== undefined ? true : undefined}
          aria-describedby={sentence !== null ? errorId : undefined}
          onChange={(event) => {
            setText(event.target.value);
            // An answer holds for what was sent. A wait the API named holds until it is over.
            if (post.isError && wait === 0) post.reset();
          }}
          onFocus={() => focusChanged(true)}
          onBlur={() => focusChanged(false)}
        />
        <Button
          ref={button}
          type="submit"
          variant="secondary"
          width="fit"
          aria-label="Send"
          className="w-12 px-0"
          loading={post.isPending}
          disabled={blocked}
          // A tap on the button leaves the focus in the field, and the keyboard up.
          onMouseDown={(event) => event.preventDefault()}
        >
          <SendHorizontalIcon aria-hidden />
        </Button>
      </div>
      {sentence !== null && <InlineError id={errorId}>{sentence}</InlineError>}
      <div className="flex items-start gap-3 type-caption text-muted-foreground">
        <RetryCountdown error={error} />
        <span data-slot="chat-count" className="ml-auto shrink-0 tabular-nums">
          {text.length} / {maxLength}
        </span>
      </div>
    </form>
  );
}
