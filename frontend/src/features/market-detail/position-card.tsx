"use client";

import type { ReactNode } from "react";

import { BeanDrop } from "@/components/feedback";
import { StakeLeftLine } from "@/components/market";
import { Amount, SignedAmount } from "@/components/money";
import { Button } from "@/components/ui/button";
import type { MarketDetailResponse } from "@/lib/api/schema";
import { formatShares, isZero } from "@/lib/format";

import { DetailSection } from "./detail-section";
import { canSell } from "./trade-surface";

/** One line of the position: what it is on the left, the API's figure for it on the right. */
function Line({
  label,
  caption,
  children,
}: {
  label: ReactNode;
  /** What the figure is, under it, where the label on the left does not say. */
  caption?: string;
  children: ReactNode;
}) {
  return (
    <div className="flex min-h-14 items-center justify-between gap-3 border-b py-2 last:border-b-0">
      <dt className="min-w-0 type-body wrap-break-word">{label}</dt>
      <dd className="flex shrink-0 flex-col items-end gap-0.5 text-right">
        {children}
        {caption !== undefined && (
          <span className="type-caption text-muted-foreground">{caption}</span>
        )}
      </dd>
    </div>
  );
}

export type PositionCardProps = {
  market: MarketDetailResponse;
  /**
   * The outcome buttons are shown (the market is OPEN or HALTED): then so are the stake
   * that is left and "Sell".
   */
  trading: boolean;
  /**
   * Set when the market resolved in front of the user and paid them: what identifies
   * that payout, for the bean drop. Null on every other render, page load included.
   */
  payoutArrival: string | null;
  onSell: () => void;
};

/**
 * "Your position" on Market detail (DESIGN.md §13), shown once the viewer has bought into
 * the market. A line for each holding whose `shares` is not `0` with what it is worth,
 * then the stake: "Your stake" with `net_stake_cr`, which a resolved market replaces by
 * "Paid out" with `payout_cr` and a voided one by "Refunded" with `refund_cr`.
 *
 * Every figure is a field of `market.viewer`, shown as sent and labelled. Nothing is
 * added up, and whether there is something to sell is read from the `allowed` flags of
 * `sell_presets`, which are the server's.
 */
export function PositionCard({ market, trading, payoutArrival, onSell }: PositionCardProps) {
  const { viewer } = market;
  const held = viewer.holdings.filter((holding) => !isZero(holding.shares));

  return (
    <DetailSection title="Your position" slot="position">
      {held.length === 0 && <p className="type-body text-muted-foreground">You hold no shares.</p>}

      <dl className="flex flex-col">
        {held.map((holding) => (
          <Line
            key={holding.outcome}
            label={formatShares(holding.shares, { outcome: holding.outcome })}
            caption="Value"
          >
            <Amount value={holding.value_cr} />
          </Line>
        ))}

        {market.status === "RESOLVED" && viewer.payout_cr !== null ? (
          <Line label="Paid out">
            <BeanDrop arrival={payoutArrival}>
              <SignedAmount value={viewer.payout_cr} />
            </BeanDrop>
          </Line>
        ) : market.status === "VOID" && viewer.refund_cr !== null ? (
          <Line label="Refunded">
            <Amount value={viewer.refund_cr} />
          </Line>
        ) : (
          <Line label="Your stake">
            <Amount value={viewer.net_stake_cr} />
          </Line>
        )}
      </dl>

      {trading && (
        <>
          <StakeLeftLine stakeLeftCr={viewer.stake_left_cr} stakeLimitCr={viewer.stake_limit_cr} />
          <Button variant="secondary" disabled={!canSell(viewer)} onClick={onSell}>
            Sell
          </Button>
        </>
      )}
    </DetailSection>
  );
}
