"use client";

import { useId, useState } from "react";

import { SignedAmount } from "@/components/money";
import { DateTime } from "@/components/time";
import type { LedgerItem } from "@/lib/api/schema";
import { cn } from "@/lib/utils";

import { rowClasses, RowFigure, RowItem, rowPressClasses, RowText } from "./list-row";

/**
 * One line of the Wallet's activity (DESIGN.md §12): the API's `description` with the
 * time below it, and the signed amount. A tap expands the row to show the full
 * `amount_cr` string, which the row itself cuts after the second decimal. The expanded
 * row also shows its description in full: on a phone a line names a market, and one
 * line is too short for most questions.
 */
export function LedgerRow({ item }: { item: LedgerItem }) {
  const [open, setOpen] = useState(false);
  const detailId = useId();

  return (
    <RowItem>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={detailId}
        onClick={() => setOpen((was) => !was)}
        className={cn(rowClasses, rowPressClasses)}
      >
        <RowText
          wrap={open}
          title={item.description}
          below={<DateTime value={item.created_at} />}
        />
        <RowFigure>
          <SignedAmount value={item.amount_cr} />
        </RowFigure>
      </button>
      <div
        id={detailId}
        hidden={!open}
        className="flex items-center justify-between gap-3 pb-3 type-body-sm text-muted-foreground"
      >
        <span>Exact amount</span>
        <SignedAmount value={item.amount_cr} full />
      </div>
    </RowItem>
  );
}
