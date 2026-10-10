"use client";

import { useMutation } from "@tanstack/react-query";
import { useId, useState } from "react";

import { InlineError } from "@/components/feedback";
import { DateTimeField, TextField } from "@/components/form";
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
import { Checkbox } from "@/components/ui/checkbox";
import { type ApiError, placeError } from "@/lib/api/errors";
import { revokeCodesMutation } from "@/lib/api/mutations";
import type { CodeBatchItem, CreateCodeBatchRequest } from "@/lib/api/schema";
import { useConfig } from "@/lib/api/session";
import { formatAmount, formatTokens } from "@/lib/format";

import { ActionDialog, type DialogFrame } from "./action-dialog";

const BATCH_FIELDS = ["label", "count", "value_cr", "expires_at", "one_per_user"] as const;

/** A number of codes is typed as digits. Whether it is one the API takes is the API's to say. */
const DIGITS = /^\d+$/;

/**
 * "New batch" (DESIGN.md §13): asks for the label, the number of codes, what each code is
 * for (filled in with `code_default_value_cr`), when the codes expire and whether a
 * person may redeem only one of them.
 *
 * What each code is for is sent as the string that was typed: it is never read as a
 * number here, and the bounds in the help text are for the reader, not checked. A value
 * outside them, a count the API does not take and a time that has passed come back as a
 * `VALIDATION_ERROR` on their field; the monthly cap as `LIMIT_EXCEEDED` above the button.
 */
export function NewBatchDialog({
  pending,
  error,
  onSubmit,
  onEdited,
  ...frame
}: DialogFrame & {
  /** The request is out, or its outcome is still being read. */
  pending: boolean;
  error: ApiError | null;
  onSubmit: (batch: CreateCodeBatchRequest) => void;
  /** The input changed, and an answer holds only for what was sent. */
  onEdited: () => void;
}) {
  const config = useConfig();
  const id = useId();
  const [label, setLabel] = useState("");
  const [count, setCount] = useState("");
  const [value, setValue] = useState(config.code_default_value_cr);
  const [expiresAt, setExpiresAt] = useState<string | null>(null);
  const [onePerUser, setOnePerUser] = useState(true);

  const placed = error ? placeError(error, BATCH_FIELDS) : null;
  const typed = { label: label.trim(), count: count.trim(), value: value.trim() };
  const ready =
    typed.label !== "" && DIGITS.test(typed.count) && typed.value !== "" && expiresAt !== null;
  const onePerUserError = placed?.fields.one_per_user;
  const onePerUserErrorId = `${id}-one-per-user-error`;

  return (
    <ActionDialog
      {...frame}
      title="New batch"
      confirm="Create batch"
      pending={pending}
      disabled={!ready}
      error={error}
      message={placed?.form}
      onConfirm={() => {
        if (expiresAt === null) return;
        onSubmit({
          label: typed.label,
          count: Number.parseInt(typed.count, 10),
          value_cr: typed.value,
          expires_at: expiresAt,
          one_per_user: onePerUser,
        });
      }}
    >
      <TextField
        label="Label"
        name="label"
        value={label}
        onChange={(event) => {
          setLabel(event.target.value);
          onEdited();
        }}
        autoComplete="off"
        error={placed?.fields.label}
      />
      <TextField
        label="Number of codes"
        name="count"
        value={count}
        onChange={(event) => {
          setCount(event.target.value);
          onEdited();
        }}
        inputMode="numeric"
        autoComplete="off"
        error={placed?.fields.count}
      />
      <TextField
        label="Tokens per code"
        name="value_cr"
        value={value}
        onChange={(event) => {
          setValue(event.target.value);
          onEdited();
        }}
        inputMode="decimal"
        autoComplete="off"
        help={`Between ${formatAmount(config.code_min_value_cr)} and ${formatTokens(config.code_max_value_cr)}`}
        error={placed?.fields.value_cr}
      />
      <DateTimeField
        label="Expires"
        name="expires_at"
        value={expiresAt}
        onChange={(next) => {
          setExpiresAt(next);
          onEdited();
        }}
        help={`${config.timezone} time.`}
        error={placed?.fields.expires_at}
      />
      <div className="flex flex-col gap-2">
        <label className="flex min-h-11 cursor-pointer items-center gap-3 type-body">
          <Checkbox
            name="one_per_user"
            checked={onePerUser}
            onCheckedChange={(checked) => {
              setOnePerUser(checked === true);
              onEdited();
            }}
            aria-describedby={onePerUserError === undefined ? undefined : onePerUserErrorId}
            aria-invalid={onePerUserError === undefined ? undefined : true}
          />
          One code per person
        </label>
        {onePerUserError !== undefined && (
          <InlineError id={onePerUserErrorId}>{onePerUserError}</InlineError>
        )}
      </div>
    </ActionDialog>
  );
}

/**
 * "Revoke" on a batch (DESIGN.md §13): an alert-dialog with a destructive button. It
 * sends `POST /admin/code-batches/{batch_id}/revoke` without `codes`, which revokes every
 * code of the batch that has not been redeemed.
 */
export function RevokeDialog({ batch, ...frame }: DialogFrame & { batch: CodeBatchItem }) {
  const revoke = useMutation(revokeCodesMutation);

  return (
    <ActionDialog
      {...frame}
      irreversible
      title="Revoke batch"
      description="Every code of this batch that has not been redeemed stops working."
      confirm="Revoke"
      destructive
      pending={revoke.isPending}
      error={revoke.error}
      message={revoke.error?.message}
      onConfirm={() => {
        if (revoke.isPending) return;
        revoke.mutate({ batchId: batch.id }, { onSuccess: frame.onClose });
      }}
    >
      <p className="type-body wrap-break-word">{batch.label}</p>
    </ActionDialog>
  );
}

/**
 * Asks before the codes of a new batch leave the screen (DESIGN.md §13): they cannot be
 * shown again. The same question stands in front of "Done", of a link that leads away and
 * of the back button; only the words for the two answers differ.
 */
export function LeaveCodesDialog({
  open,
  leaving,
  from,
  onStay,
  onLeave,
  onCloseAutoFocus,
}: {
  open: boolean;
  /** True when the user is on the way to another page; false when they tapped "Done". */
  leaving: boolean;
  /**
   * What the user tapped to get here: "Done", or the link that was held back. Null for
   * the back button, which is not on the page.
   */
  from: HTMLElement | null;
  onStay: () => void;
  onLeave: () => void;
  /** Where the focus goes once the dialog has closed, given what the user had tapped. */
  onCloseAutoFocus: (event: Event, from: HTMLElement | null) => void;
}) {
  // What the question is about is known while it stands, and needed once it has gone:
  // for the focus, and so that the words do not change while the dialog fades out.
  const [asked, setAsked] = useState({ from, leaving });
  if (open && (from !== asked.from || leaving !== asked.leaving)) setAsked({ from, leaving });
  const asker = open ? from : asked.from;
  const away = open ? leaving : asked.leaving;

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onStay();
      }}
    >
      <AlertDialogContent onCloseAutoFocus={(event) => onCloseAutoFocus(event, asker)}>
        <AlertDialogHeader>
          <AlertDialogTitle>{away ? "Leave this page?" : "Close the codes?"}</AlertDialogTitle>
          <AlertDialogDescription>These codes cannot be shown again.</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>{away ? "Stay" : "Cancel"}</AlertDialogCancel>
          <Button variant="destructive" onClick={onLeave}>
            {away ? "Leave" : "Close codes"}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
