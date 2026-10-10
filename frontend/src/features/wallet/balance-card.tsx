"use client";

import { BeanDrop } from "@/components/feedback";
import { Amount } from "@/components/money";
import { Day } from "@/components/time";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { MeResponse } from "@/lib/api/schema";
import { formatAmount } from "@/lib/format";

/** Tokens that arrived in front of the user: a redeemed code. */
export type Arrival = {
  /** Tells one arrival from the next. */
  id: number;
  /** The balance that was on screen before, shown until the beans have landed. */
  before: string;
};

/**
 * The balance card of the Wallet (DESIGN.md §13): the token icon and `balance_cr` in
 * `display`, labelled "Your tokens", and below it when the next monthly tokens come and
 * how many. Every figure is a field of `GET /me`, shown as sent.
 *
 * The bean drop plays into the balance when a code is redeemed (DESIGN.md §7), once per
 * `arrival` and never when the Wallet opens: tokens booked while nobody was looking
 * simply show.
 */
export function BalanceCard({
  me,
  arrival,
}: {
  me: Pick<MeResponse, "balance_cr" | "monthly_grant_cr" | "next_monthly_grant_on">;
  arrival: Arrival | null;
}) {
  return (
    <Card data-slot="balance-card" className="gap-2">
      <p className="type-body-sm text-muted-foreground">Your tokens</p>
      <BeanDrop arrival={arrival?.id ?? null}>
        {(falling) => (
          <Amount
            value={falling && arrival !== null ? arrival.before : me.balance_cr}
            size="display"
          />
        )}
      </BeanDrop>
      <p className="type-body-sm text-muted-foreground">
        Next monthly tokens: {formatAmount(me.monthly_grant_cr)} on{" "}
        <Day value={me.next_monthly_grant_on} />
      </p>
    </Card>
  );
}

/** The balance card while the Wallet's first read is on its way: the same three lines. */
export function BalanceCardSkeleton() {
  return (
    <Card data-slot="balance-card-skeleton" aria-hidden className="gap-2">
      <Skeleton className="h-5 w-24" />
      <Skeleton className="h-13 w-40" />
      <Skeleton className="h-5 w-56 max-w-full" />
    </Card>
  );
}
