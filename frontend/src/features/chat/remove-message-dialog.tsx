"use client";

import { toast } from "sonner";

import { SubmitError } from "@/components/feedback";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { messageOf } from "@/lib/api/errors";
import { removeChatMessageMutation } from "@/lib/api/mutations";
import type { ChatMessageItem } from "@/lib/api/schema";
import { useSubmit } from "@/lib/api/submit";
import { useRetryAfter } from "@/lib/hooks";

import { ChatLine } from "./chat-log";

/**
 * The confirmation of a removal (DESIGN.md §13, "Removing a message"): a confirmation
 * dialog that states the consequence and shows the line it is about, as the log shows it.
 * It sends `POST /admin/markets/{market_id}/chat/{message_id}/remove`.
 *
 * The dialog cannot be closed while its request is out, so the answer is seen where it
 * was asked for: the toast "Message removed", or the API's sentence above the buttons. A
 * message that is gone already answers `NOT_FOUND`, and the chat is read again.
 */
export function RemoveMessageDialog({
  marketId,
  message,
  open,
  onClose,
  onRemoved,
  onCloseAutoFocus,
}: {
  marketId: string;
  /** The line the dialog was opened for. It stays what it was while the log moves on. */
  message: ChatMessageItem;
  open: boolean;
  /** Asked to close: by "Cancel", the close cross or Escape, and after the removal. */
  onClose: () => void;
  /** The answer of the removal: the id of the message that is gone. */
  onRemoved: (messageId: string) => void;
  /** Where the focus goes once the dialog has closed: no trigger of the primitive opened it. */
  onCloseAutoFocus: (event: Event) => void;
}) {
  const remove = useSubmit(removeChatMessageMutation);
  const wait = useRetryAfter(remove.error);
  const pending = remove.isPending;

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !pending) onClose();
      }}
    >
      <AlertDialogContent locked={pending} onCloseAutoFocus={onCloseAutoFocus}>
        <AlertDialogHeader>
          <AlertDialogTitle>Remove this message?</AlertDialogTitle>
          <AlertDialogDescription>
            It disappears for everyone. This cannot be taken back.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <ChatLine message={message} className="overflow-hidden" />
        <SubmitError
          error={remove.error}
          message={remove.error === null ? null : messageOf(remove.error)}
        />
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>Cancel</AlertDialogCancel>
          <Button
            variant="destructive"
            loading={pending}
            disabled={wait > 0}
            onClick={() => {
              // A second tap sends nothing while the first is out (DESIGN.md §14).
              remove.mutate(
                { marketId, messageId: message.id },
                {
                  onSuccess: (removed) => {
                    onRemoved(removed.id);
                    toast("Message removed");
                    onClose();
                  },
                },
              );
            }}
          >
            Remove message
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
