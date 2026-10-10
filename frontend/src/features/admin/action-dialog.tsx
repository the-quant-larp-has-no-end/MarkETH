"use client";

import { type FormEvent, type ReactNode, useEffect, useRef } from "react";

import { SubmitError } from "@/components/feedback";
import { revealFirstInvalid } from "@/components/form";
import { SheetBody, SheetFooter } from "@/components/sheet";
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
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { ApiError } from "@/lib/api/errors";
import { useRetryAfter } from "@/lib/hooks";

/** What the screen that opens a dialog decides about it. */
export type DialogFrame = {
  open: boolean;
  /** Asked to close: by "Cancel", the close button, Escape or a tap beside a plain dialog. */
  onClose: () => void;
  /**
   * Where the focus goes once the dialog has closed (DESIGN.md §15). A dialog that is
   * opened by state has no trigger the primitive could return to, so its opener says.
   */
  onCloseAutoFocus?: (event: Event) => void;
};

export type ActionDialogProps = DialogFrame & {
  /**
   * An action that cannot be taken back: the dialog is an alert-dialog, states the
   * consequence in `description`, and does not close on a tap beside it.
   */
  irreversible?: boolean;
  title: string;
  /** The consequence of the action, in one or two sentences. */
  description?: string;
  /** The fields the action asks for. */
  children?: ReactNode;
  /** The failure of the request, and its message when it names none of the fields. */
  error: ApiError | null;
  message: string | null | undefined;
  /** The label of the button that goes ahead. */
  confirm: string;
  /** Reject, void and revoke go ahead on a `destructive` button. */
  destructive?: boolean;
  /** The request is out and has not been answered. */
  pending: boolean;
  /** Something the action asks for is still missing. */
  disabled?: boolean;
  onConfirm: () => void;
};

/**
 * The frame of every dialog of the admin area (DESIGN.md §13): a title, the consequence,
 * the fields, the API's message for a refusal that names no field, then "Cancel" and the
 * button that goes ahead. The fields scroll; the buttons stay.
 *
 * The button that goes ahead is disabled from tap until the answer, and for the time a
 * `RATE_LIMITED` names. While the request is out the dialog cannot be closed, so the
 * answer is never lost: a refusal is shown in here, as the API sent it.
 */
export function ActionDialog({
  open,
  onClose,
  onCloseAutoFocus,
  irreversible = false,
  title,
  description,
  children,
  error,
  message,
  confirm,
  destructive = false,
  pending,
  disabled = false,
  onConfirm,
}: ActionDialogProps) {
  const wait = useRetryAfter(error);
  const blocked = disabled || wait > 0;
  const form = useRef<HTMLFormElement>(null);

  // The fields scroll under the buttons: a refused one is brought into view.
  useEffect(() => {
    if (error !== null) revealFirstInvalid(form.current);
  }, [error]);

  const onOpenChange = (next: boolean) => {
    if (!next && !pending) onClose();
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (pending || blocked) return;
    onConfirm();
  };
  const go = (
    <Button
      type="submit"
      variant={destructive ? "destructive" : "primary"}
      loading={pending}
      disabled={blocked}
    >
      {confirm}
    </Button>
  );
  const refusal = <SubmitError error={error} message={message} />;

  if (irreversible) {
    return (
      <AlertDialog open={open} onOpenChange={onOpenChange}>
        <AlertDialogContent
          className="gap-0 overflow-hidden p-0"
          onCloseAutoFocus={onCloseAutoFocus}
        >
          <form ref={form} noValidate onSubmit={submit} className="flex min-h-0 flex-1 flex-col">
            <AlertDialogHeader className="px-4 pt-4">
              <AlertDialogTitle>{title}</AlertDialogTitle>
            </AlertDialogHeader>
            <SheetBody>
              <AlertDialogDescription>{description}</AlertDialogDescription>
              {children}
            </SheetBody>
            <SheetFooter>
              {refusal}
              <AlertDialogFooter>
                <AlertDialogCancel disabled={pending}>Cancel</AlertDialogCancel>
                {go}
              </AlertDialogFooter>
            </SheetFooter>
          </form>
        </AlertDialogContent>
      </AlertDialog>
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="gap-0 p-0"
        showCloseButton={!pending}
        onCloseAutoFocus={onCloseAutoFocus}
        // Without a description, say so: the dialog must not point at a text that is not there.
        {...(description === undefined ? { "aria-describedby": undefined } : {})}
      >
        <form ref={form} noValidate onSubmit={submit} className="flex min-h-0 flex-1 flex-col">
          <DialogHeader className="px-4 pt-4">
            <DialogTitle>{title}</DialogTitle>
          </DialogHeader>
          <SheetBody>
            {description !== undefined && <DialogDescription>{description}</DialogDescription>}
            {children}
          </SheetBody>
          <SheetFooter>
            {refusal}
            <DialogFooter>
              <DialogClose asChild>
                <Button variant="ghost" disabled={pending}>
                  Cancel
                </Button>
              </DialogClose>
              {go}
            </DialogFooter>
          </SheetFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
