"use client";

import { useState } from "react";

import { AmountField, CheckboxField, DateTimeField, TextField } from "@/components/form";
import { type ApiError, placeError } from "@/lib/api/errors";
import { revokeCodesMutation } from "@/lib/api/mutations";
import type { CodeBatchItem, CreateCodeBatchRequest } from "@/lib/api/schema";
import { useConfig } from "@/lib/api/session";
import { useSubmit } from "@/lib/api/submit";
import { formatAmount, formatCount, formatTokens } from "@/lib/format";

import { ActionDialog, type DialogFrame } from "./action-dialog";

const BATCH_FIELDS = ["label", "count", "value_cr", "expires_at", "one_per_user"] as const;

/**
 * "New batch" (DESIGN.md §13): asks for the label, the number of codes, what each code is
 * for (filled in with `code_default_value_cr`), when the codes expire and whether a
 * person may redeem only one of them.
 *
 * Every bound it states is one of `/config`, and none of them is checked here: the label
 * counts its characters against `code_label_max_length`, the number of codes names
 * `code_batch_max_count`, what a code is for names `code_min_value_cr` and
 * `code_max_value_cr`. What each code is for is sent as the string that was typed and is
 * never read as a number. A value outside the bounds, a count the API does not take and a
 * time that has passed come back as a `VALIDATION_ERROR` on their field; the monthly cap
 * as `LIMIT_EXCEEDED` above the button.
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
  const [label, setLabel] = useState("");
  const [count, setCount] = useState("");
  const [value, setValue] = useState(config.code_default_value_cr);
  const [expiresAt, setExpiresAt] = useState<string | null>(null);
  const [onePerUser, setOnePerUser] = useState(true);

  const placed = error ? placeError(error, BATCH_FIELDS) : null;
  const typedLabel = label.trim();
  const ready = typedLabel !== "" && count !== "" && value !== "" && expiresAt !== null;

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
          label: typedLabel,
          // A number of codes is a whole number in the request; the field takes digits alone.
          count: Number.parseInt(count, 10),
          value_cr: value,
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
        maxLength={config.code_label_max_length}
        showCount
        autoComplete="off"
        error={placed?.fields.label}
      />
      <AmountField
        whole
        label="Number of codes"
        name="count"
        value={count}
        onChange={(next) => {
          setCount(next);
          onEdited();
        }}
        help={`At most ${formatCount(config.code_batch_max_count)} codes`}
        error={placed?.fields.count}
      />
      <AmountField
        label="Tokens per code"
        name="value_cr"
        value={value}
        onChange={(next) => {
          setValue(next);
          onEdited();
        }}
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
      <CheckboxField
        name="one_per_user"
        checked={onePerUser}
        onChange={(checked) => {
          setOnePerUser(checked);
          onEdited();
        }}
        error={placed?.fields.one_per_user}
      >
        One code per person
      </CheckboxField>
    </ActionDialog>
  );
}

/**
 * "Revoke" on a batch (DESIGN.md §13): an alert-dialog with a destructive button. It
 * sends `POST /admin/code-batches/{batch_id}/revoke` without `codes`, which revokes every
 * code of the batch that has not been redeemed.
 */
export function RevokeDialog({ batch, ...frame }: DialogFrame & { batch: CodeBatchItem }) {
  const revoke = useSubmit(revokeCodesMutation);

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
        // A second tap sends nothing while the first is out (DESIGN.md §14).
        revoke.mutate({ batchId: batch.id }, { onSuccess: frame.onClose });
      }}
    >
      <p className="type-body wrap-break-word">{batch.label}</p>
    </ActionDialog>
  );
}
