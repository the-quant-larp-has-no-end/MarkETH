"use client";

import { type FormEvent, useState } from "react";
import { toast } from "sonner";

import { SubmitError } from "@/components/feedback";
import { StatusBadge } from "@/components/market";
import { Link } from "@/components/shell";
import { Button } from "@/components/ui/button";
import { PolicyCheckbox } from "@/features/policy";
import { placeError } from "@/lib/api/errors";
import { requestBroadcastMutation } from "@/lib/api/mutations";
import type { MarketBroadcastResponse } from "@/lib/api/schema";
import { useConfig } from "@/lib/api/session";
import { useSubmit } from "@/lib/api/submit";
import { useRetryAfter } from "@/lib/hooks";
import { cn } from "@/lib/utils";

import { endReasonSentence } from "./words";

const FIELDS = ["policy_accepted"] as const;
const LINE = "type-body-sm text-muted-foreground";

/**
 * Whether the answer is about a request of the caller's own that is not a live stream
 * (yet): they may ask for one, they wait for VIS, VIS did not approve, or their stream
 * has ended. The studio then says so, where it would otherwise have nothing to show.
 */
export function showsProposer({ broadcast, viewer }: MarketBroadcastResponse): boolean {
  if (viewer.can_request) return true;
  if (broadcast === null || !viewer.is_broadcaster) return false;
  return (
    broadcast.status === "REQUESTED" ||
    broadcast.status === "REFUSED" ||
    broadcast.status === "ENDED"
  );
}

/**
 * The studio before there is a live stream to send (DESIGN.md §13, the rows "For the
 * proposer, and for whoever streams"): what became of the caller's request, in the
 * sentences of the section "Live stream" of Market detail, and what the caller may do
 * next.
 *
 * | The answer says                     | The studio shows                                  |
 * | ----------------------------------- | ------------------------------------------------- |
 * | REQUESTED, the caller's own         | "In review" and that VIS decides                  |
 * | APPROVED, the caller's own          | that it is approved                               |
 * | REFUSED, the caller's own           | that it was not approved, with the admin's reason |
 * | ENDED, the caller's own             | that it has ended, with the sentence of its end   |
 * | `can_request`                       | the request: the sentence, the checkbox of the    |
 * |                                     | usage policy and "Request a live stream"          |
 * | `can_start` or `can_start_as_admin` | "Set up the live stream" (`onSetUp`)              |
 *
 * `onSetUp` is passed while the caller may start. The camera is asked for by that tap and
 * not before: an approval reaches this page by itself, and a camera that came on because
 * an admin tapped "Approve" would be nobody's decision.
 */
export function ProposerBroadcast({
  marketId,
  answer,
  onSetUp,
}: {
  marketId: string;
  answer: MarketBroadcastResponse;
  /** The caller may go live, and asks for the set-up with its camera. */
  onSetUp?: () => void;
}) {
  const { broadcast: config } = useConfig();
  const { broadcast, viewer } = answer;
  const own = broadcast !== null && viewer.is_broadcaster ? broadcast : null;
  const ended = own?.status === "ENDED" ? endReasonSentence(own.end_reason, config) : null;

  return (
    <>
      {own?.status === "REQUESTED" && (
        <div className="flex flex-col items-start gap-2">
          <StatusBadge status="PROPOSED" />
          <p className={LINE}>You asked for a live stream. VIS decides on it.</p>
        </div>
      )}
      {own?.status === "APPROVED" && (
        <p className={LINE}>Your live stream is approved. Start it when you are ready.</p>
      )}
      {own?.status === "REFUSED" && (
        <div className="flex flex-col gap-1">
          <p className="type-body-sm">Your request was not approved.</p>
          {own.refusal_reason !== null && (
            <p className={cn(LINE, "wrap-break-word whitespace-pre-line")}>{own.refusal_reason}</p>
          )}
        </div>
      )}
      {own?.status === "ENDED" && (
        <p className={LINE}>
          Your live stream has ended.
          {ended !== null && ` ${ended}`}
          {viewer.can_start && " You can go live again without asking."}
        </p>
      )}

      {viewer.can_request && <RequestForm marketId={marketId} />}

      <div className="flex flex-col gap-3 md:flex-row">
        {onSetUp !== undefined && (
          <Button variant="secondary" onClick={onSetUp}>
            Set up the live stream
          </Button>
        )}
        <Button variant={onSetUp === undefined ? "secondary" : "ghost"} asChild>
          <Link href={`/markets/${marketId}`}>Back to the market</Link>
        </Button>
      </div>
    </>
  );
}

/**
 * The request for a live stream: `POST /markets/{market_id}/broadcast/request` with
 * `policy_accepted: true`, stated by the form whose box is ticked. The box is not ticked
 * for whoever comes back. A refusal shows the API's `message` above the button, and one
 * that names `policy_accepted` under the checkbox (DESIGN.md §14).
 */
function RequestForm({ marketId }: { marketId: string }) {
  // A second submit is stopped where it is made, not by what the last render knew.
  const request = useSubmit(requestBroadcastMutation);
  const [accepted, setAccepted] = useState(false);
  const wait = useRetryAfter(request.error);
  const placed = request.error === null ? null : placeError(request.error, FIELDS);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!accepted || wait > 0) return;
    request.mutate(
      { marketId, policy_accepted: true },
      { onSuccess: () => void toast("Request sent. VIS decides on it.") },
    );
  };

  return (
    <form noValidate onSubmit={submit} className="flex flex-col gap-3">
      <p className={LINE}>
        Ask VIS to let you stream this market live, from your camera. An admin decides on every
        request.
      </p>
      <PolicyCheckbox
        checked={accepted}
        onChange={setAccepted}
        error={placed?.fields.policy_accepted}
      />
      <SubmitError error={request.error} message={placed?.form} />
      <Button
        type="submit"
        variant="secondary"
        loading={request.isPending}
        disabled={!accepted || wait > 0}
      >
        Request a live stream
      </Button>
    </form>
  );
}
