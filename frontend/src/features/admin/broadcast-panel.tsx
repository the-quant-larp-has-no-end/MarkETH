"use client";

import { useQuery } from "@tanstack/react-query";
import { type RefObject, useEffect, useId, useRef, useState } from "react";
import { toast } from "sonner";

import { QueryState, SubmitError } from "@/components/feedback";
import { TextAreaField } from "@/components/form";
import { isProposal, LiveBadge } from "@/components/market";
import { Link, TextLink } from "@/components/shell";
import { DateTime } from "@/components/time";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { messageOf, placeError } from "@/lib/api/errors";
import {
  approveBroadcastMutation,
  endBroadcastMutation,
  refuseBroadcastMutation,
} from "@/lib/api/mutations";
import { marketBroadcastQuery, marketQuery } from "@/lib/api/queries";
import type { BroadcastItem, MarketBroadcastResponse } from "@/lib/api/schema";
import { useConfig } from "@/lib/api/session";
import { useSubmit } from "@/lib/api/submit";
import { formatTime } from "@/lib/format";
import { useRetryAfter } from "@/lib/hooks";
import { liveTopic, useLiveTopic } from "@/lib/live";
import { cn } from "@/lib/utils";

import { ActionDialog, type DialogFrame } from "./action-dialog";

export type BroadcastPanelProps = {
  /** The market whose live stream the block is about: the `[id]` of `/admin/markets/[id]`. */
  marketId: string;
};

/** The two actions of the block that are confirmed in a dialog. */
type Confirmed = "refuse" | "end";

type BroadcastDialogProps = DialogFrame & {
  marketId: string;
  /** The live stream the dialog was opened for, whatever the block behind it shows by now. */
  broadcastId: string;
};

/**
 * "Refuse" (DESIGN.md §13): a confirmation dialog that asks for the reason, which the
 * person who asked for the live stream reads. It sends
 * `POST /admin/broadcasts/{broadcast_id}/refuse` with `reason`.
 */
function RefuseDialog({ marketId, broadcastId, ...frame }: BroadcastDialogProps) {
  const config = useConfig();
  const [reason, setReason] = useState("");
  const refuse = useSubmit(refuseBroadcastMutation);
  const placed = refuse.error ? placeError(refuse.error, ["reason"]) : null;
  const typed = reason.trim();

  return (
    <ActionDialog
      {...frame}
      irreversible
      title="Refuse request"
      description="The requester sees your reason."
      confirm="Refuse request"
      destructive
      pending={refuse.isPending}
      disabled={typed === ""}
      error={refuse.error}
      message={placed?.form}
      onConfirm={() => {
        // A second tap sends nothing while the first is out (DESIGN.md §14).
        refuse.mutate(
          { marketId, broadcastId, reason: typed },
          {
            onSuccess: () => {
              toast("Request refused");
              frame.onClose();
            },
          },
        );
      }}
    >
      <TextAreaField
        label="Reason"
        name="reason"
        value={reason}
        onChange={(event) => {
          setReason(event.target.value);
          // An answer is about what was sent: it holds until the reason changes.
          if (refuse.isError) refuse.reset();
        }}
        maxLength={config.short_text_max_length}
        showCount
        error={placed?.fields.reason}
      />
    </ActionDialog>
  );
}

/**
 * "End stream" (DESIGN.md §13): the confirmation the studio asks for as well. It sends
 * `POST /broadcasts/{broadcast_id}/end`; the API answers a stream that has ended already
 * the same way.
 */
function EndDialog({ marketId, broadcastId, ...frame }: BroadcastDialogProps) {
  const end = useSubmit(endBroadcastMutation);

  return (
    <ActionDialog
      {...frame}
      irreversible
      title="End the live stream?"
      description="Viewers see that it has ended. No picture and no sound is kept."
      confirm="End stream"
      destructive
      pending={end.isPending}
      error={end.error}
      message={end.error === null ? null : messageOf(end.error)}
      onConfirm={() => {
        end.mutate(
          { marketId, broadcastId },
          {
            onSuccess: () => {
              toast("Live stream ended");
              frame.onClose();
            },
          },
        );
      }}
    />
  );
}

/**
 * What the block says about the live stream of its market: one sentence per status
 * (DESIGN.md §13). A status arrives as a key and is never shown as one. Who asked, and
 * every instant, are the API's; where it sends none, the sentence does without it.
 */
function Sentence({ broadcast }: { broadcast: BroadcastItem | null }) {
  const { timezone } = useConfig();

  if (broadcast === null) return <>No live stream.</>;
  const person = broadcast.requested_by;

  switch (broadcast.status) {
    case "REQUESTED":
      return (
        <>
          {person === null ? "Requested" : `Requested by ${person.username} (${person.email})`} on{" "}
          <DateTime value={broadcast.requested_at} />.
        </>
      );
    case "APPROVED":
      return <>{person === null ? "Approved." : `Approved. ${person.username} can go live.`}</>;
    case "LIVE":
      if (broadcast.started_at === null) return <>Live.</>;
      // The start of a stream that is on is its time of day alone (DESIGN.md §10).
      return (
        <>
          Live since{" "}
          <time dateTime={broadcast.started_at}>{formatTime(broadcast.started_at, timezone)}</time>.
        </>
      );
    case "ENDED":
      if (broadcast.ended_at === null) return <>No live stream.</>;
      return (
        <>
          The last live stream ended on <DateTime value={broadcast.ended_at} />.
        </>
      );
    case "REFUSED":
      return <>No live stream.</>;
  }
}

/** A line and a button, in the shape of the block while its read is on its way. */
function PanelSkeleton() {
  return (
    <div data-slot="broadcast-panel-skeleton" role="status" className="flex flex-col gap-3">
      <span className="sr-only">Loading</span>
      <Skeleton className="h-5 w-2/3" />
      <Skeleton className="h-12 md:w-40" />
    </div>
  );
}

/**
 * The block once its read has answered: the sentence of the status, then the buttons. A
 * button is shown exactly while its flag of `viewer` is true: no role, no status of the
 * market and no clock says what an admin may do.
 *
 * "Approve" is sent by its tap. "Refuse" and "End stream" are confirmed in a dialog, which
 * stays open when the live stream changes behind it, so that the API's message for a
 * refused action can be read there; the block behind it shows what the stream is by then.
 */
function Panel({
  marketId,
  answer: { broadcast, viewer },
  returnFocusTo,
}: {
  marketId: string;
  answer: MarketBroadcastResponse;
  /**
   * Where the focus goes after an action took its own button off the screen: the heading
   * of the block, from where a screen reader reads what the live stream is now.
   */
  returnFocusTo: RefObject<HTMLElement | null>;
}) {
  const approve = useSubmit(approveBroadcastMutation);
  const wait = useRetryAfter(approve.error);
  // `round` gives every opening a dialog of its own, with an empty field and no answer in it.
  const [dialog, setDialog] = useState<{
    action: Confirmed;
    broadcastId: string;
    round: number;
  } | null>(null);
  const [open, setOpen] = useState(false);
  const opener = useRef<HTMLButtonElement | null>(null);
  const approveButton = useRef<HTMLButtonElement | null>(null);

  const live = broadcast?.status === "LIVE";
  // "No live stream." and the end of the last one say that nothing is on: the quiet lines.
  const quiet =
    broadcast === null || broadcast.status === "REFUSED" || broadcast.status === "ENDED";
  // A refusal of "Approve" is about the request it was sent for.
  const refused =
    approve.error !== null && approve.variables?.broadcastId === broadcast?.id
      ? approve.error
      : null;

  const ask = (action: Confirmed, broadcastId: string, from: HTMLButtonElement) => {
    opener.current = from;
    // The answer to an earlier tap gives way to what the admin does next.
    if (approve.isError) approve.reset();
    setDialog((last) => ({ action, broadcastId, round: (last?.round ?? 0) + 1 }));
    setOpen(true);
  };
  const frame: DialogFrame = {
    open,
    onClose: () => setOpen(false),
    onCloseAutoFocus: (event) => {
      event.preventDefault();
      const from = opener.current;
      const back = from !== null && from.isConnected && !from.disabled;
      (back ? from : returnFocusTo.current)?.focus();
    },
  };

  return (
    <>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        {live && <LiveBadge />}
        {/* Announced when it changes: a request that arrives, a stream that goes live or ends. */}
        <p
          role="status"
          className={cn("min-w-0 type-body-sm wrap-break-word", quiet && "text-muted-foreground")}
        >
          <Sentence broadcast={broadcast} />
        </p>
        {live && <TextLink href={`/markets/${marketId}`}>Watch</TextLink>}
      </div>
      <SubmitError error={refused} message={refused === null ? null : messageOf(refused)} />
      {(viewer.can_start_as_admin ||
        (broadcast !== null && (viewer.can_approve || viewer.can_refuse || viewer.can_end))) && (
        <div data-slot="broadcast-actions" className="flex flex-col gap-2 md:flex-row">
          {viewer.can_start_as_admin && (
            <Button variant="secondary" asChild>
              <Link href={`/markets/${marketId}/live`}>Start a live stream</Link>
            </Button>
          )}
          {broadcast !== null && viewer.can_approve && (
            <Button
              ref={approveButton}
              variant="secondary"
              loading={approve.isPending}
              disabled={wait > 0}
              onClick={() => {
                // A second tap sends nothing while the first is out (DESIGN.md §14).
                approve.mutate(
                  { marketId, broadcastId: broadcast.id },
                  {
                    onSuccess: () => {
                      toast("Request approved");
                      // The answer takes "Approve" off the screen: the focus it held, or
                      // lost while it waited, goes to the heading of the block.
                      const held = document.activeElement;
                      if (
                        held === null ||
                        held === document.body ||
                        held === approveButton.current
                      ) {
                        returnFocusTo.current?.focus();
                      }
                    },
                  },
                );
              }}
            >
              Approve
            </Button>
          )}
          {broadcast !== null && viewer.can_refuse && (
            <Button
              variant="destructive"
              disabled={approve.isPending}
              onClick={(event) => ask("refuse", broadcast.id, event.currentTarget)}
            >
              Refuse
            </Button>
          )}
          {broadcast !== null && viewer.can_end && (
            <Button
              variant="destructive"
              onClick={(event) => ask("end", broadcast.id, event.currentTarget)}
            >
              End stream
            </Button>
          )}
        </div>
      )}
      {dialog?.action === "refuse" && (
        <RefuseDialog
          key={`refuse-${dialog.round}`}
          marketId={marketId}
          broadcastId={dialog.broadcastId}
          {...frame}
        />
      )}
      {dialog?.action === "end" && (
        <EndDialog
          key={`end-${dialog.round}`}
          marketId={marketId}
          broadcastId={dialog.broadcastId}
          {...frame}
        />
      )}
    </>
  );
}

/**
 * Admin: the block "Live stream" of one market (`/admin/markets/[id]`, DESIGN.md §13),
 * from `GET /markets/{market_id}/broadcast`: what the live stream of the market is, and
 * what this admin may do about it.
 *
 * | The answer says              | The block shows                           | Buttons, by flag     |
 * | ---------------------------- | ----------------------------------------- | -------------------- |
 * | none, or the latest REFUSED  | "No live stream."                         | "Start a live        |
 * |                              |                                           | stream", a link to   |
 * |                              |                                           | the studio           |
 * | the latest is ENDED          | "The last live stream ended on …"         | the same             |
 * | REQUESTED                    | "Requested by … (…) on …"                 | "Approve", "Refuse"  |
 * | APPROVED                     | "Approved. … can go live."                | "Refuse"             |
 * | LIVE                         | the Live badge, "Live since …", "Watch"   | "End stream"         |
 *
 * It is absent while that read answers `NOT_FOUND`, as it does for a proposal. While the
 * read is on its way it shows a skeleton, and a read that failed shows the banner with
 * "Try again", as every part of the admin area does. It shows no count of viewers.
 *
 * It names the live stream of its market to the event stream (`broadcast:{id}`): a
 * `broadcast.changed`, every stream that opens and every poll read the answer again
 * (`lib/live/rules.ts`), so a request shows without a reload. One thing no event and no
 * write of the block says: that the market itself changed. What an admin may do about a
 * live stream follows the status of its market, so the block reads its answer again
 * whenever the status the cache holds for the market changes: after "Approve" on a
 * proposal the block is there, without a reload.
 *
 * That status is watched in the cache and never asked for, and nothing an admin may do is
 * taken from it: the read is made for every market, and the flags of its answer decide.
 * It has one more use, which is where the skeleton stands. The skeleton holds the place of
 * the block, so that the action bar under it keeps its place when the answer arrives. On a
 * market the cache shows as a proposal the answer is `NOT_FOUND` and the block absent, so
 * there no place is held while the read is on its way.
 */
export function BroadcastPanel({ marketId }: BroadcastPanelProps) {
  const id = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const broadcast = useQuery(marketBroadcastQuery(marketId));
  useLiveTopic(liveTopic.broadcast(marketId));

  // The status of the market as the screen behind the block has it: watched, never asked for.
  const { data: marketStatus } = useQuery({
    ...marketQuery(marketId),
    enabled: false,
    select: (market) => market.status,
  });
  const { refetch } = broadcast;
  const readFor = useRef(marketStatus);
  useEffect(() => {
    if (readFor.current === marketStatus) return;
    readFor.current = marketStatus;
    void refetch();
  }, [marketStatus, refetch]);

  if (broadcast.error?.code === "NOT_FOUND") return null;
  // A proposal has no live stream to wait for: nothing stands in its place meanwhile.
  const awaited = broadcast.data === undefined && broadcast.error === null;
  if (awaited && marketStatus !== undefined && isProposal(marketStatus)) return null;

  return (
    <section aria-labelledby={id} data-slot="broadcast-panel" className="flex flex-col gap-3">
      <h2 id={id} ref={heading} tabIndex={-1} className="type-h3">
        Live stream
      </h2>
      <QueryState query={broadcast} skeleton={<PanelSkeleton />}>
        {(answer) => <Panel marketId={marketId} answer={answer} returnFocusTo={heading} />}
      </QueryState>
    </section>
  );
}
