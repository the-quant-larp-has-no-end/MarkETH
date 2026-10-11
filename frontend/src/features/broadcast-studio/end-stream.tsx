"use client";

import { useState } from "react";

import { SubmitError } from "@/components/feedback";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { messageOf } from "@/lib/api/errors";
import { endBroadcastMutation } from "@/lib/api/mutations";
import { useSubmit } from "@/lib/api/submit";
import { useRetryAfter } from "@/lib/hooks";

/**
 * "End stream" (DESIGN.md §13, "Live stream studio"): the destructive button and the
 * dialog that confirms it. The end of a live stream cannot be taken back, so it is asked
 * for twice: the dialog states the consequence and sends
 * `POST /broadcasts/{broadcast_id}/end` on its own "End stream".
 *
 * While the request is out the dialog cannot be closed, and a refusal is shown in it, as
 * the API sent it. The stream goes on until the API has answered that it ended; `onEnded`
 * is where the studio then stops sending.
 */
export function EndStream({
  marketId,
  broadcastId,
  onEnded,
}: {
  marketId: string;
  broadcastId: string;
  /** The API answered: the live stream has ended. */
  onEnded?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const end = useSubmit(endBroadcastMutation);
  const wait = useRetryAfter(end.error);
  const pending = end.isPending;

  const confirm = () => {
    end.mutate(
      { marketId, broadcastId },
      {
        onSuccess: () => {
          setOpen(false);
          onEnded?.();
        },
      },
    );
  };

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (pending) return;
        // A refusal belongs to the attempt it answered, not to the next opening.
        if (next) end.reset();
        setOpen(next);
      }}
    >
      <AlertDialogTrigger asChild>
        <Button variant="destructive">End stream</Button>
      </AlertDialogTrigger>
      <AlertDialogContent locked={pending}>
        <AlertDialogHeader>
          <AlertDialogTitle>End the live stream?</AlertDialogTitle>
          <AlertDialogDescription>
            Viewers see that it has ended. No picture and no sound is kept.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <SubmitError error={end.error} message={end.error === null ? null : messageOf(end.error)} />
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>Cancel</AlertDialogCancel>
          <Button variant="destructive" loading={pending} disabled={wait > 0} onClick={confirm}>
            End stream
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
