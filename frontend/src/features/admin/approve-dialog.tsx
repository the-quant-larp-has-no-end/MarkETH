"use client";

import { useEffect, useId, useRef, useState } from "react";

import { InlineError } from "@/components/feedback";
import { TextField } from "@/components/form";
import { tierName } from "@/components/market";
import { Amount } from "@/components/money";
import { Button } from "@/components/ui/button";
import { radioCardClasses, RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import {
  type MarketTerms,
  MarketTermsFields,
  statedTerms,
  TERM_FIELDS,
  type TermField,
} from "@/features/propose";
import { limitName, placeError } from "@/lib/api/errors";
import { approveMarketMutation } from "@/lib/api/mutations";
import type { ApproveMarketRequest, MarketDetailResponse, MarketTier } from "@/lib/api/schema";
import { useConfig } from "@/lib/api/session";
import { formatTokens, toDateTimeInput } from "@/lib/format";

import { ActionDialog } from "./action-dialog";
import type { MarketDialogProps } from "./market-dialogs";
import { useMarketAction } from "./use-market-action";

const FIELDS = ["tier", "budget_cr", ...TERM_FIELDS] as const;

function termsOf(market: MarketDetailResponse): MarketTerms {
  return {
    question: market.question,
    yes_definition: market.yes_definition,
    no_definition: market.no_definition,
    resolution_criteria: market.resolution_criteria,
    resolution_source: market.resolution_source,
    close_at: market.close_at,
    resolve_by: market.resolve_by,
  };
}

/**
 * Approve (PROPOSED): opens the market in one step (DESIGN.md §13). It asks for a tier:
 * one of the three of `/config`, each with its liquidity b and its loss budget, or Custom
 * with a budget of the admin's choice. The admin picks it: none is chosen when the dialog
 * opens. The wording and the two times can be changed on the way.
 *
 * It sends `tier`, `budget_cr` for CUSTOM exactly as it was typed, and each field that
 * was changed; a field left alone is left out and keeps what the proposal says. Whether a
 * budget is within `max_market_budget_cr` and whether the caps leave room is the API's to
 * say: `LIMIT_EXCEEDED` shows its message in here, and the market is read again.
 */
export function ApproveDialog({ market, ...frame }: MarketDialogProps) {
  const config = useConfig();
  const id = useId();
  const tiers = config.tiers.filter((entry) => entry.tier !== "CUSTOM");
  // What the proposal said when the dialog opened: a change is measured against this.
  const [proposed] = useState(() => termsOf(market));
  const [terms, setTerms] = useState(proposed);
  const [editing, setEditing] = useState(false);
  const [tier, setTier] = useState<MarketTier | "">("");
  const [budget, setBudget] = useState("");
  const action = useMarketAction(approveMarketMutation, market.id, frame.onClose);
  const toggle = useRef<HTMLButtonElement>(null);

  // The fields open below the button, under the fold of the dialog: show where they are.
  useEffect(() => {
    if (editing) toggle.current?.scrollIntoView({ block: "start" });
  }, [editing]);

  const placed = action.error ? placeError(action.error, FIELDS) : null;
  const overBudget =
    action.error !== null && limitName(action.error) === "MAX_MARKET_BUDGET" && tier === "CUSTOM";
  const budgetError = placed?.fields.budget_cr ?? (overBudget ? action.error?.message : undefined);
  const tierError = placed?.fields.tier;
  // An error on a field that is folded away would be an error nobody sees.
  const refusedTerm = TERM_FIELDS.some((field) => placed?.fields[field] !== undefined);
  const showTerms = editing || refusedTerm;

  const stated = statedTerms(terms);
  const typedBudget = budget.trim();
  const ready = tier !== "" && (tier !== "CUSTOM" || typedBudget !== "") && stated !== null;

  /** Two spellings of one instant are no change: a time is compared as the field shows it. */
  const changed = (field: TermField, value: string) =>
    field === "close_at" || field === "resolve_by"
      ? toDateTimeInput(value, config.timezone) !==
        toDateTimeInput(proposed[field] ?? "", config.timezone)
      : value !== proposed[field];

  const approve = () => {
    if (tier === "" || stated === null) return;
    const body: ApproveMarketRequest = { tier };
    if (tier === "CUSTOM") body.budget_cr = typedBudget;
    for (const field of TERM_FIELDS) {
      if (changed(field, stated[field])) body[field] = stated[field];
    }
    action.send(body);
  };

  const labelId = `${id}-tier`;
  const tierErrorId = `${id}-tier-error`;
  const termsId = `${id}-terms`;

  return (
    <ActionDialog
      {...frame}
      title="Approve market"
      confirm="Approve"
      pending={action.pending}
      disabled={!ready}
      error={action.error}
      message={overBudget ? null : placed?.form}
      onConfirm={approve}
    >
      <div data-slot="field" className="flex flex-col gap-2">
        <span id={labelId} className="type-caption text-muted-foreground">
          Tier
        </span>
        <RadioGroup
          name="tier"
          value={tier}
          onValueChange={(next) => {
            setTier(next as MarketTier);
            action.edited();
          }}
          aria-labelledby={labelId}
          aria-describedby={tierError === undefined ? undefined : tierErrorId}
          aria-invalid={tierError === undefined ? undefined : true}
          className="gap-2"
        >
          {tiers.map((entry) => {
            const nameId = `${id}-${entry.tier}-name`;
            const figuresId = `${id}-${entry.tier}-figures`;
            return (
              <label key={entry.tier} className={radioCardClasses}>
                <RadioGroupItem
                  value={entry.tier}
                  aria-labelledby={nameId}
                  aria-describedby={figuresId}
                  className="mt-0.5"
                />
                <span className="flex min-w-0 flex-1 flex-col gap-1">
                  <span id={nameId} className="type-label">
                    {tierName(entry.tier)}
                  </span>
                  <span
                    id={figuresId}
                    className="flex flex-wrap gap-x-4 gap-y-1 type-body-sm text-muted-foreground"
                  >
                    {/* The blanks are for a screen reader, which hears the words run together. */}
                    <span className="inline-flex items-center gap-2">
                      Liquidity b{" "}
                      <Amount value={entry.b_cr} size="sm" className="text-foreground" />
                    </span>{" "}
                    <span className="inline-flex items-center gap-2">
                      Loss budget{" "}
                      <Amount value={entry.max_loss_cr} size="sm" className="text-foreground" />
                    </span>
                  </span>
                </span>
              </label>
            );
          })}
          <label className={radioCardClasses}>
            <RadioGroupItem
              value="CUSTOM"
              aria-labelledby={`${id}-CUSTOM-name`}
              className="mt-0.5"
            />
            <span id={`${id}-CUSTOM-name`} className="type-label">
              {tierName("CUSTOM")}
            </span>
          </label>
        </RadioGroup>
        {tierError !== undefined && <InlineError id={tierErrorId}>{tierError}</InlineError>}
      </div>

      {tier === "CUSTOM" && (
        <TextField
          label="Loss budget"
          name="budget_cr"
          value={budget}
          onChange={(event) => {
            setBudget(event.target.value);
            action.edited();
          }}
          inputMode="decimal"
          autoComplete="off"
          help={`At most ${formatTokens(config.max_market_budget_cr)}.`}
          error={budgetError}
        />
      )}

      <Button
        ref={toggle}
        variant="secondary"
        size="compact"
        width="fit"
        aria-expanded={showTerms}
        aria-controls={termsId}
        onClick={() => {
          // Folding the fields away drops what was changed in them: nothing is sent unseen.
          if (showTerms) setTerms(proposed);
          setEditing(!showTerms);
          action.edited();
        }}
      >
        Edit wording and times
      </Button>
      <div id={termsId} className="flex flex-col gap-4" hidden={!showTerms}>
        {showTerms && (
          <MarketTermsFields
            value={terms}
            onChange={(next) => {
              setTerms(next);
              action.edited();
            }}
            errors={placed?.fields}
          />
        )}
      </div>
    </ActionDialog>
  );
}
