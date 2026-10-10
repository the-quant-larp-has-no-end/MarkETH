"use client";

import { useMutation } from "@tanstack/react-query";
import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { toast } from "sonner";

import { SubmitError } from "@/components/feedback";
import { revealFirstInvalid } from "@/components/form";
import { Button } from "@/components/ui/button";
import { placeError } from "@/lib/api/errors";
import { proposeMarketMutation } from "@/lib/api/mutations";
import type { MarketTier } from "@/lib/api/schema";
import { useConfig } from "@/lib/api/session";
import { formatCount } from "@/lib/format";
import { useRetryAfter } from "@/lib/hooks";

import {
  EMPTY_TERMS,
  type MarketTerms,
  MarketTermsFields,
  statedTerms,
  TERM_FIELDS,
} from "./market-terms-fields";
import { TierCards } from "./tier-cards";
import { startingTier } from "./tiers";

const FIELDS = [...TERM_FIELDS, "requested_tier"] as const;

/** The content rules of SPEC §6, in the short form of DESIGN.md §13. */
const CONTENT_RULES = [
  "No private matters of named people",
  "No grades",
  "No outcomes one trader can decide",
  "No party politics or religion",
] as const;

/**
 * The propose form (DESIGN.md §13): one field per row, in the order "Question", "YES
 * means", "NO means", "How it is decided", "Source", "Trading closes", "Decided by",
 * "Liquidity", then the content rules and "Submit for review", which sends
 * `POST /markets`.
 *
 * The API decides what is valid. A `VALIDATION_ERROR` appears on the field it names,
 * `LIMIT_EXCEEDED` above the button, both with the API's message, and the input stays.
 * Without an answer the banner stands above the button and the button waits until the
 * list of proposals has been read again: a proposal that arrived is in it.
 *
 * `onSent` is called once a proposal was accepted; whoever renders the form empties it
 * by giving it another `key`.
 */
export function ProposeForm({ onSent }: { onSent?: () => void }) {
  const config = useConfig();
  const propose = useMutation(proposeMarketMutation);
  const [terms, setTerms] = useState<MarketTerms>(EMPTY_TERMS);
  const [tier, setTier] = useState<MarketTier | "">(() => startingTier(config));
  const wait = useRetryAfter(propose.error);
  const rulesId = useId();
  const form = useRef<HTMLFormElement>(null);

  // The button is at the end of a long form; a refused field may be far above it.
  useEffect(() => {
    if (propose.error !== null) revealFirstInvalid(form.current);
  }, [propose.error]);

  const placed = propose.error ? placeError(propose.error, FIELDS) : null;
  const stated = statedTerms(terms);
  const filled = stated !== null && tier !== "";

  /** An answer is about what was sent: once the input changes, it no longer applies. */
  const edited = () => {
    if (propose.isError) propose.reset();
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (stated === null || tier === "" || propose.isPending) return;
    propose.mutate(
      { ...stated, requested_tier: tier },
      {
        onSuccess: () => {
          toast("Proposal sent");
          onSent?.();
        },
      },
    );
  };

  return (
    <form
      ref={form}
      noValidate
      onSubmit={submit}
      className="flex flex-col gap-4"
      data-slot="propose-form"
    >
      <MarketTermsFields
        value={terms}
        onChange={(next) => {
          setTerms(next);
          edited();
        }}
        errors={placed?.fields}
      />
      <TierCards
        value={tier}
        onChange={(next) => {
          setTier(next);
          edited();
        }}
        error={placed?.fields.requested_tier}
      />

      <div className="flex flex-col gap-2 type-body-sm text-muted-foreground">
        <p id={rulesId} className="type-caption">
          Content rules
        </p>
        <ul aria-labelledby={rulesId} className="flex list-disc flex-col gap-1 pl-5">
          {CONTENT_RULES.map((rule) => (
            <li key={rule}>{rule}</li>
          ))}
        </ul>
        <p>You cannot trade a market you propose.</p>
        <p>
          {config.max_proposals_per_day === 1
            ? "You can propose 1 market a day."
            : `You can propose ${formatCount(config.max_proposals_per_day)} markets a day.`}
        </p>
      </div>

      <SubmitError error={propose.error} message={placed?.form} />
      {propose.error?.outcomeUnknown === true && (
        <p className="type-body-sm text-muted-foreground">
          If your proposal arrived, it is in the list below.
        </p>
      )}

      <Button type="submit" loading={propose.isPending} disabled={!filled || wait > 0}>
        Submit for review
      </Button>
    </form>
  );
}
