"use client";

import { type ReactNode, useId } from "react";

import { StatusBadge, tierName } from "@/components/market";
import { Amount, Missing } from "@/components/money";
import { DateTime } from "@/components/time";
import type { MarketDetailResponse, MarketResolution } from "@/lib/api/schema";

function Section({ title, children }: { title: string; children: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <h2 id={id} className="type-h3">
        {title}
      </h2>
      {children}
    </section>
  );
}

/** A label with a text of its own below it: the wording of a market. */
function Statement({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <dt className="type-caption text-muted-foreground">{term}</dt>
      <dd className="type-body-sm wrap-break-word whitespace-pre-line">{children}</dd>
    </div>
  );
}

/** A label and a short value side by side: one fact. */
function Fact({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div className="flex min-h-11 items-center justify-between gap-4 border-b py-2 last:border-b-0">
      <dt className="shrink-0 type-body-sm text-muted-foreground">{term}</dt>
      <dd className="min-w-0 text-right type-body-sm wrap-break-word">{children}</dd>
    </div>
  );
}

/**
 * The rules of a market, as Market detail states them (DESIGN.md §13): "YES means", "NO
 * means", "How it is decided", "Source", "Trading closes" and "Decided by".
 */
export function MarketRules({ market }: { market: MarketDetailResponse }) {
  return (
    <Section title="Rules">
      <dl className="flex flex-col gap-3">
        <Statement term="YES means">{market.yes_definition}</Statement>
        <Statement term="NO means">{market.no_definition}</Statement>
        <Statement term="How it is decided">{market.resolution_criteria}</Statement>
        <Statement term="Source">{market.resolution_source}</Statement>
        <Statement term="Trading closes">
          <DateTime value={market.close_at} />
        </Statement>
        <Statement term="Decided by">
          <DateTime value={market.resolve_by} />
        </Statement>
      </dl>
    </Section>
  );
}

/**
 * How a market ended: the outcome badge, the evidence and when it was settled. For a
 * voided market the evidence is the reason VIS gave.
 */
export function Resolution({ resolution }: { resolution: MarketResolution }) {
  return (
    <Section title="Resolution">
      <div className="flex flex-col items-start gap-2">
        <StatusBadge
          status={resolution.outcome === "VOID" ? "VOID" : "RESOLVED"}
          outcome={resolution.outcome}
        />
        <p className="type-body-sm wrap-break-word whitespace-pre-line">{resolution.evidence}</p>
        <DateTime value={resolution.resolved_at} className="type-caption text-muted-foreground" />
      </div>
    </Section>
  );
}

/**
 * The facts only an admin needs (DESIGN.md §13): who proposed the market, its dates, the
 * reason it was rejected with, and how it is priced: its tier, its liquidity b and its
 * loss budget, which the API sends to admins alone. Every value is the API's, shown as
 * sent; one it has not got yet is a dash.
 *
 * A market has its three pricing figures from its approval on. Until then the API names
 * no tier, and the screen shows a dash for all three.
 */
export function MarketFacts({ market }: { market: MarketDetailResponse }) {
  // The two figures of a tier count only once there is a tier.
  const liquidity = market.tier === null ? null : market.b_cr;
  const lossBudget = market.tier === null ? null : market.max_loss_cr;

  return (
    <Section title="Details">
      <dl className="flex flex-col">
        <Fact term="Proposed by">{market.proposer_email ?? <Missing />}</Fact>
        <Fact term="Proposed">
          <DateTime value={market.created_at} />
        </Fact>
        <Fact term="Opened">
          {market.opened_at === null ? <Missing /> : <DateTime value={market.opened_at} />}
        </Fact>
        <Fact term="Closed">
          {market.closed_at === null ? <Missing /> : <DateTime value={market.closed_at} />}
        </Fact>
        {market.rejection_reason !== null && (
          <Fact term="Rejection reason">{market.rejection_reason}</Fact>
        )}
        <Fact term="Tier">{market.tier === null ? <Missing /> : tierName(market.tier)}</Fact>
        <Fact term="Liquidity b">
          {liquidity === null ? <Missing /> : <Amount value={liquidity} />}
        </Fact>
        <Fact term="Loss budget">
          {lossBudget === null ? <Missing /> : <Amount value={lossBudget} />}
        </Fact>
      </dl>
    </Section>
  );
}
