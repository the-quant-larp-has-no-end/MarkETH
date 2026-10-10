"use client";

import { useId } from "react";

import { InlineError } from "@/components/feedback";
import { tierExplanation, tierName } from "@/components/market";
import { radioCardClasses, RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import type { MarketTier } from "@/lib/api/schema";
import { useConfig } from "@/lib/api/session";

import { proposableTiers } from "./tiers";

/**
 * "Liquidity" of the propose form (DESIGN.md §13): one radio card per tier of `/config`,
 * each with a plain name and one line that says what the tier does to the price. The
 * figures of a tier are VIS's business and are not shown here.
 */
export function TierCards({
  value,
  onChange,
  error,
  disabled = false,
}: {
  /** The chosen tier, or the empty string while none is chosen. */
  value: MarketTier | "";
  onChange: (tier: MarketTier) => void;
  /** The API's message for `requested_tier`. */
  error?: string | null;
  disabled?: boolean;
}) {
  const config = useConfig();
  const id = useId();
  const labelId = `${id}-label`;
  const errorId = `${id}-error`;
  const hasError = typeof error === "string" && error !== "";

  return (
    <div data-slot="field" className="flex flex-col gap-2">
      <span id={labelId} className="type-caption text-muted-foreground">
        Liquidity
      </span>
      <RadioGroup
        name="requested_tier"
        value={value}
        onValueChange={(next) => onChange(next as MarketTier)}
        aria-labelledby={labelId}
        aria-describedby={hasError ? errorId : undefined}
        aria-invalid={hasError ? true : undefined}
        disabled={disabled}
        className="gap-2"
      >
        {proposableTiers(config).map((tier) => {
          const nameId = `${id}-${tier}-name`;
          const explanationId = `${id}-${tier}-explanation`;
          const explanation = tierExplanation(tier);
          return (
            <label key={tier} className={radioCardClasses}>
              <RadioGroupItem
                value={tier}
                aria-labelledby={nameId}
                aria-describedby={explanation === undefined ? undefined : explanationId}
                className="mt-0.5"
              />
              <span className="flex min-w-0 flex-col gap-0.5">
                <span id={nameId} className="type-label">
                  {tierName(tier)}
                </span>
                {explanation !== undefined && (
                  <span id={explanationId} className="type-body-sm text-muted-foreground">
                    {explanation}
                  </span>
                )}
              </span>
            </label>
          );
        })}
      </RadioGroup>
      {hasError && <InlineError id={errorId}>{error}</InlineError>}
    </div>
  );
}
