"use client";

import { StatusBadge } from "@/components/market";
import { DateTime } from "@/components/time";
import type { MarketDetailResponse, MarketResolution } from "@/lib/api/schema";

import { DetailSection, Fact } from "./detail-section";

/**
 * The rules of a market (DESIGN.md §13): what counts as YES and as NO, how and from what
 * it is decided, and its two times. The wording is the market's own, shown as sent and
 * in full; the times are shown in the timezone of `/config`.
 */
export function RulesSection({ market }: { market: MarketDetailResponse }) {
  return (
    <DetailSection title="Rules" slot="rules">
      <dl className="flex flex-col gap-4">
        <Fact label="YES means">{market.yes_definition}</Fact>
        <Fact label="NO means">{market.no_definition}</Fact>
        <Fact label="How it is decided">{market.resolution_criteria}</Fact>
        <Fact label="Source">{market.resolution_source}</Fact>
        <Fact label="Trading closes">
          <DateTime value={market.close_at} />
        </Fact>
        <Fact label="Decided by">
          <DateTime value={market.resolve_by} />
        </Fact>
      </dl>
    </DetailSection>
  );
}

/**
 * What happens to stakes if the market is voided: the API's `void_rule`, in full and
 * never collapsed. SPEC §6 requires it to be visible before anyone trades.
 */
export function VoidRuleSection({ rule }: { rule: string }) {
  return (
    <DetailSection title="If the market is voided" slot="void-rule">
      <p className="type-body wrap-break-word whitespace-pre-line">{rule}</p>
    </DetailSection>
  );
}

/**
 * How a market ended (DESIGN.md §13): the outcome badge, the evidence VIS gave (for a
 * voided market, its reason) and when it was decided.
 */
export function ResolutionSection({
  market,
  resolution,
}: {
  market: Pick<MarketDetailResponse, "status" | "outcome">;
  resolution: MarketResolution;
}) {
  return (
    <DetailSection
      title="Resolution"
      slot="resolution"
      aside={<StatusBadge status={market.status} outcome={market.outcome} />}
    >
      <p className="type-body wrap-break-word whitespace-pre-line">{resolution.evidence}</p>
      <p className="type-caption text-muted-foreground">
        <DateTime value={resolution.resolved_at} />
      </p>
    </DetailSection>
  );
}

/** Why VIS did not approve a proposal: the `rejection_reason`, which its proposer sees. */
export function RejectionSection({ reason }: { reason: string }) {
  return (
    <DetailSection title="Reason" slot="rejection">
      <p className="type-body wrap-break-word whitespace-pre-line">{reason}</p>
    </DetailSection>
  );
}
