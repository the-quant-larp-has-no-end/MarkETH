"use client";

import { CircleCheckIcon } from "lucide-react";
import { type ReactNode, useEffect, useRef } from "react";

import { BeanBounce } from "@/components/feedback";
import { Amount } from "@/components/money";
import { SheetBody, SheetFooter } from "@/components/sheet";
import { Button } from "@/components/ui/button";
import type { TradeResponse } from "@/lib/api/schema";
import { useConfig } from "@/lib/api/session";
import { formatAmount, formatPercent, formatTokens } from "@/lib/format";

import { receiptHeadline } from "./intent";

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex min-h-6 items-center justify-between gap-3">
      <dt className="type-body-sm text-muted-foreground">{label}</dt>
      <dd className="flex items-center justify-end type-number">{children}</dd>
    </div>
  );
}

/**
 * The receipt of an executed trade (DESIGN.md §13), in place of the form: a check icon,
 * "Bought 183.29 YES shares" or "Sold …", what was paid or received, the probability
 * after the trade, the balance, and "Done".
 *
 * Every figure is a field of the trade's answer, shown as sent. A receipt is a detail
 * view, so amounts and shares are the whole string (DESIGN.md §10). The token icon of the
 * amount bounces once (DESIGN.md §7); with reduced motion it stands still.
 */
export function TradeReceipt({ trade, onDone }: { trade: TradeResponse; onDone: () => void }) {
  const { price_display_decimals } = useConfig();
  const headline = useRef<HTMLHeadingElement>(null);

  // The button that was tapped is gone with the form: put the focus on the result, so
  // that a screen reader says it and the next Tab reaches "Done".
  useEffect(() => {
    headline.current?.focus({ preventScroll: true });
  }, []);

  return (
    <>
      <SheetBody>
        <div className="flex flex-col items-start gap-3">
          <CircleCheckIcon aria-hidden className="size-8 text-foreground" />
          <h2 ref={headline} tabIndex={-1} className="type-h2 wrap-break-word outline-none">
            {receiptHeadline(trade)}
          </h2>
        </div>

        <dl className="flex flex-col gap-2 rounded-md bg-background p-3">
          <Row label={trade.side === "BUY" ? "You paid" : "You received"}>
            <span data-slot="amount" className="inline-flex items-center gap-1 whitespace-nowrap">
              <BeanBounce size={16} />
              <span aria-hidden>{formatAmount(trade.amount_cr, { full: true })}</span>
              <span className="sr-only">{formatTokens(trade.amount_cr, { full: true })}</span>
            </span>
          </Row>
          <Row label="New probability">
            {formatPercent(trade.p_yes_after, price_display_decimals)} YES
          </Row>
          <Row label="Balance">
            <Amount value={trade.balance_cr} full />
          </Row>
        </dl>
      </SheetBody>

      <SheetFooter>
        <Button width="full" onClick={onDone}>
          Done
        </Button>
      </SheetFooter>
    </>
  );
}
