"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";

import { ErrorBanner, InlineError } from "@/components/feedback";
import { TextAreaField } from "@/components/form";
import { OutcomeToggle } from "@/components/market";
import { Amount, Missing } from "@/components/money";
import { Skeleton } from "@/components/ui/skeleton";
import { placeError } from "@/lib/api/errors";
import { keys } from "@/lib/api/keys";
import {
  closeMarketMutation,
  haltMarketMutation,
  rejectMarketMutation,
  resolveMarketMutation,
  resumeMarketMutation,
  voidMarketMutation,
} from "@/lib/api/mutations";
import { riskQuery } from "@/lib/api/queries";
import type { MarketDetailResponse, Outcome } from "@/lib/api/schema";
import { useConfig } from "@/lib/api/session";

import { ActionDialog, type DialogFrame } from "./action-dialog";
import { type MarketActionOptions, useMarketAction } from "./use-market-action";

export type MarketDialogProps = DialogFrame & {
  /** The market the action is for, as the screen behind the dialog shows it. */
  market: MarketDetailResponse;
};

/** Who reads a reason or a note that only the audit log keeps. */
const AUDIT_HELP = "Kept in the audit log.";

type TextActionProps<Body> = MarketDialogProps & {
  options: MarketActionOptions<Body>;
  /** The body for what was typed, without the blanks around it; empty when nothing was. */
  toBody: (text: string) => Body;
  /** The name the API gives the text, which is also where it reports an error on it. */
  field: "reason" | "note";
  label: string;
  help?: string;
  /** The action goes ahead without the text. */
  optional?: boolean;
  title: string;
  description?: string;
  irreversible?: boolean;
  confirm: string;
  destructive?: boolean;
};

/** An action that asks for one text: a reason or a note, as long as `/config` allows. */
function TextAction<Body>({
  market,
  options,
  toBody,
  field,
  label,
  help,
  optional = false,
  title,
  description,
  irreversible,
  confirm,
  destructive,
  ...frame
}: TextActionProps<Body>) {
  const config = useConfig();
  const [text, setText] = useState("");
  const action = useMarketAction(options, market.id, frame.onClose);
  const placed = action.error ? placeError(action.error, [field]) : null;
  const typed = text.trim();

  return (
    <ActionDialog
      {...frame}
      irreversible={irreversible}
      title={title}
      description={description}
      confirm={confirm}
      destructive={destructive}
      pending={action.pending}
      disabled={!optional && typed === ""}
      error={action.error}
      message={placed?.form}
      onConfirm={() => action.send(toBody(typed))}
    >
      <TextAreaField
        label={label}
        name={field}
        value={text}
        onChange={(event) => {
          setText(event.target.value);
          action.edited();
        }}
        maxLength={config.short_text_max_length}
        showCount
        help={help}
        error={placed?.fields[field]}
      />
    </ActionDialog>
  );
}

/** Reject (PROPOSED): a reason, which the proposer sees. It cannot be taken back. */
export function RejectDialog(props: MarketDialogProps) {
  return (
    <TextAction
      {...props}
      options={rejectMarketMutation}
      toBody={(reason) => ({ reason })}
      field="reason"
      label="Reason"
      title="Reject proposal"
      description="The proposer sees your reason. A rejected proposal stays rejected."
      irreversible
      confirm="Reject"
      destructive
    />
  );
}

/** Halt (OPEN): a reason. Trading pauses until the market is resumed. */
export function HaltDialog(props: MarketDialogProps) {
  return (
    <TextAction
      {...props}
      options={haltMarketMutation}
      toBody={(reason) => ({ reason })}
      field="reason"
      label="Reason"
      help={AUDIT_HELP}
      title="Halt market"
      confirm="Halt"
    />
  );
}

/** Resume (HALTED): a note, which may be left out. */
export function ResumeDialog(props: MarketDialogProps) {
  return (
    <TextAction
      {...props}
      options={resumeMarketMutation}
      toBody={(note) => (note === "" ? {} : { note })}
      field="note"
      label="Note (optional)"
      help={AUDIT_HELP}
      optional
      title="Resume market"
      confirm="Resume"
    />
  );
}

/** Close (OPEN, HALTED): a note, which may be left out. It cannot be taken back. */
export function CloseDialog(props: MarketDialogProps) {
  return (
    <TextAction
      {...props}
      options={closeMarketMutation}
      toBody={(note) => (note === "" ? {} : { note })}
      field="note"
      label="Note (optional)"
      help={AUDIT_HELP}
      optional
      title="Close market"
      description="Trading stops for good. After this the market can only be resolved or voided."
      irreversible
      confirm="Close market"
    />
  );
}

/**
 * The outcome a market is resolved to: the outcome toggle of the trade sheet (DESIGN.md
 * §12) under a label, with neither segment chosen to begin with. Which outcome won is
 * never a default.
 */
function OutcomeChoice({
  value,
  onChange,
  error,
}: {
  value: Outcome | null;
  onChange: (outcome: Outcome) => void;
  error?: string | null;
}) {
  const id = useId();
  const labelId = `${id}-label`;
  const errorId = `${id}-error`;
  const hasError = typeof error === "string" && error !== "";

  return (
    <div data-slot="field" className="flex flex-col gap-2">
      <span id={labelId} className="type-caption text-muted-foreground">
        Outcome
      </span>
      <OutcomeToggle
        value={value}
        onChange={onChange}
        labelledBy={labelId}
        describedBy={hasError ? errorId : undefined}
      />
      {hasError && <InlineError id={errorId}>{error}</InlineError>}
    </div>
  );
}

/** Resolve (CLOSED): the outcome, YES or NO, and the evidence. It cannot be taken back. */
export function ResolveDialog({ market, ...frame }: MarketDialogProps) {
  const config = useConfig();
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [evidence, setEvidence] = useState("");
  const action = useMarketAction(resolveMarketMutation, market.id, frame.onClose);
  const placed = action.error ? placeError(action.error, ["outcome", "evidence"]) : null;
  const typed = evidence.trim();

  return (
    <ActionDialog
      {...frame}
      irreversible
      title="Resolve market"
      description="Everyone who holds the winning outcome is paid. This cannot be changed afterwards."
      // The button names what it will do, so the outcome is read once more before the tap.
      confirm={outcome === null ? "Resolve" : `Resolve ${outcome}`}
      pending={action.pending}
      disabled={outcome === null || typed === ""}
      error={action.error}
      message={placed?.form}
      onConfirm={() => {
        if (outcome !== null) action.send({ outcome, evidence: typed });
      }}
    >
      <OutcomeChoice
        value={outcome}
        onChange={(next) => {
          setOutcome(next);
          action.edited();
        }}
        error={placed?.fields.outcome}
      />
      <TextAreaField
        label="Evidence"
        name="evidence"
        value={evidence}
        onChange={(event) => {
          setEvidence(event.target.value);
          action.edited();
        }}
        maxLength={config.long_text_max_length}
        showCount
        error={placed?.fields.evidence}
      />
    </ActionDialog>
  );
}

/**
 * Void (CLOSED): a reason, which is published as the market's resolution. The dialog
 * states the market's `void_rule` as the consequence and, from this market's row of
 * `GET /admin/risk`, what a void costs VIS now. That figure is read afresh for every
 * opening and never shown from an earlier read; until it is there, and when it cannot be
 * read, the void cannot be confirmed.
 */
export function VoidDialog({ market, ...frame }: MarketDialogProps) {
  const config = useConfig();
  const client = useQueryClient();
  const [reason, setReason] = useState("");
  const action = useMarketAction(voidMarketMutation, market.id, frame.onClose);
  const risk = useQuery({ ...riskQuery, enabled: frame.open, refetchOnMount: "always" });
  const placed = action.error ? placeError(action.error, ["reason"]) : null;
  const typed = reason.trim();

  const unread = risk.error !== null && !risk.isFetching;
  const read = frame.open && !risk.isFetching && risk.error === null && risk.data !== undefined;
  const row = read ? risk.data.markets.find((entry) => entry.id === market.id) : undefined;
  // The risk page lists every market that is not settled. One that is missing there has
  // been settled since the screen behind this dialog was read: show it as it is.
  const settledSince = read && row === undefined;
  useEffect(() => {
    if (!settledSince) return;
    void client.invalidateQueries({ queryKey: keys.markets.detail(market.id) });
  }, [settledSince, client, market.id]);

  return (
    <ActionDialog
      {...frame}
      irreversible
      title="Void market"
      description={market.void_rule}
      confirm="Void"
      destructive
      pending={action.pending}
      disabled={typed === "" || row === undefined}
      error={action.error}
      message={placed?.form}
      onConfirm={() => action.send({ reason: typed })}
    >
      <div
        data-slot="void-cost"
        className="flex min-h-11 items-center justify-between gap-3 rounded-md bg-background px-3 py-2"
      >
        <span className="type-body-sm text-muted-foreground">What a void costs VIS now</span>{" "}
        {row !== undefined ? (
          <Amount value={row.void_cost_cr} />
        ) : settledSince ? (
          <Missing />
        ) : (
          !unread && (
            <span role="status">
              <span className="sr-only">Loading</span>
              <Skeleton className="h-5 w-16" />
            </span>
          )
        )}
      </div>
      {unread && risk.error !== null && (
        <ErrorBanner error={risk.error} onRetry={() => void risk.refetch()} />
      )}
      <TextAreaField
        label="Reason"
        name="reason"
        value={reason}
        onChange={(event) => {
          setReason(event.target.value);
          action.edited();
        }}
        maxLength={config.long_text_max_length}
        showCount
        help="Published as the market's resolution."
        error={placed?.fields.reason}
      />
    </ActionDialog>
  );
}
