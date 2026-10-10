"use client";

import { type FormEvent, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { SubmitError } from "@/components/feedback";
import { revealFirstInvalid } from "@/components/form";
import { Button } from "@/components/ui/button";
import { PolicyCheckbox } from "@/features/policy";
import { placeError } from "@/lib/api/errors";
import { proposeMarketMutation } from "@/lib/api/mutations";
import { useConfig } from "@/lib/api/session";
import { useSubmit } from "@/lib/api/submit";
import { formatCount } from "@/lib/format";
import { useRetryAfter } from "@/lib/hooks";

import {
  EMPTY_TERMS,
  type MarketTerms,
  MarketTermsFields,
  statedTerms,
  TERM_FIELDS,
} from "./market-terms-fields";

const FIELDS = [...TERM_FIELDS, "policy_accepted"] as const;

/** What the toast says once a proposal is in. The proposal itself stands in the list below. */
const PROPOSAL_SENT = "Proposal sent. VIS reviews it before it opens.";

/**
 * The propose form (DESIGN.md §13): one field per row, in the order "Question", "YES
 * means", "NO means", "How it is decided", "Source", "Trading closes", "Decided by",
 * then the two notes, the checkbox "I accept the usage policy" and "Submit for review",
 * which sends `POST /markets` with the seven fields and `policy_accepted: true`.
 *
 * The form asks nothing about how the market is priced: VIS sets that when it approves.
 * It states no content rules either: what a market may and may not be about is said in
 * the usage policy, which the checkbox links to.
 *
 * The API decides what is valid. A `VALIDATION_ERROR` appears on the field it names (one
 * that names `policy_accepted` under the checkbox), `LIMIT_EXCEEDED` above the button,
 * both with the API's message, and the input stays.
 * Without an answer the banner stands above the button and the button waits until the
 * list of proposals has been read again: a proposal that arrived is in it.
 *
 * A proposal that was accepted is told with a toast; its row is at the top of the list
 * below. `onSent` is called then; whoever renders the form empties it by giving it another
 * `key`, which also leaves the box unticked: the policy is accepted with every proposal.
 */
export function ProposeForm({ onSent }: { onSent?: () => void }) {
  const config = useConfig();
  // A second submit is stopped where it is made, not by what the last render knew.
  const propose = useSubmit(proposeMarketMutation);
  const [terms, setTerms] = useState<MarketTerms>(EMPTY_TERMS);
  // The policy is accepted with every proposal: the box is empty whenever the form is.
  const [accepted, setAccepted] = useState(false);
  const wait = useRetryAfter(propose.error);
  const form = useRef<HTMLFormElement>(null);

  // The button is at the end of a long form; a refused field may be far above it.
  useEffect(() => {
    if (propose.error !== null) revealFirstInvalid(form.current);
  }, [propose.error]);

  const placed = propose.error ? placeError(propose.error, FIELDS) : null;
  const stated = statedTerms(terms);
  const filled = stated !== null && accepted;

  /** An answer is about what was sent: it holds until the input changes. */
  const edited = () => {
    if (propose.isError) propose.reset();
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (stated === null || !accepted || wait > 0) return;
    propose.mutate(
      // The box is ticked: the proposal says what the form shows.
      { ...stated, policy_accepted: true },
      {
        onSuccess: () => {
          toast(PROPOSAL_SENT);
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

      <div
        data-slot="propose-notes"
        className="flex flex-col gap-2 type-body-sm text-muted-foreground"
      >
        <p>You cannot trade a market you propose.</p>
        <p>
          {config.max_proposals_per_day === 1
            ? "You can propose 1 market a day."
            : `You can propose ${formatCount(config.max_proposals_per_day)} markets a day.`}
        </p>
      </div>

      <PolicyCheckbox
        checked={accepted}
        onChange={(next) => {
          setAccepted(next);
          edited();
        }}
        error={placed?.fields.policy_accepted}
      />

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
