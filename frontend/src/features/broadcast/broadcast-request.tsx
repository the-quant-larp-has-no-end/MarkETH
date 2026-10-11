"use client";

import { type FormEvent, useState } from "react";
import { toast } from "sonner";

import { SubmitError } from "@/components/feedback";
import { Button } from "@/components/ui/button";
import { PolicyCheckbox } from "@/features/policy";
import { placeError } from "@/lib/api/errors";
import { requestBroadcastMutation } from "@/lib/api/mutations";
import { useSubmit } from "@/lib/api/submit";
import { useRetryAfter } from "@/lib/hooks";

const FIELDS = ["policy_accepted"] as const;

/** What the toast says once a request is in. The section shows the request from then on. */
const REQUEST_SENT = "Request sent. VIS decides on it.";

/**
 * The proposer of a market asks for its live stream (DESIGN.md §13, "Live stream"): the
 * sentence, the checkbox "I accept the usage policy", not ticked, and "Request a live
 * stream", which is disabled until the box is ticked and sends
 * `POST /markets/{market_id}/broadcast/request` with `policy_accepted: true`.
 *
 * It is shown while `viewer.can_request` is true and decides nothing itself. A refusal
 * shows the API's `message` above the button, and one that names `policy_accepted` under
 * the checkbox. The answer is the live stream of the market from then on, so the section
 * shows "In review" in the place of this form without a read of its own.
 */
export function BroadcastRequest({ marketId }: { marketId: string }) {
  // A second submit is stopped where it is made, not by what the last render knew.
  const request = useSubmit(requestBroadcastMutation);
  const [accepted, setAccepted] = useState(false);
  const wait = useRetryAfter(request.error);
  const placed = request.error ? placeError(request.error, FIELDS) : null;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!accepted || wait > 0) return;
    // The box is ticked: the request says what the form shows.
    request.mutate(
      { marketId, policy_accepted: true },
      { onSuccess: () => void toast(REQUEST_SENT) },
    );
  };

  return (
    <form
      noValidate
      onSubmit={submit}
      className="flex flex-col gap-3"
      data-slot="live-stream-request"
    >
      <p className="type-body-sm text-muted-foreground">
        Ask VIS to let you stream this market live, from your camera. An admin decides on every
        request.
      </p>

      <PolicyCheckbox
        checked={accepted}
        onChange={(next) => {
          setAccepted(next);
          // An answer is about what was sent: it holds until the input changes.
          if (request.isError) request.reset();
        }}
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
