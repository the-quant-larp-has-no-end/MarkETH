"use client";

import { useId } from "react";

import { EmptyState, QueryState } from "@/components/feedback";
import { PositionRow, RowList } from "@/components/rows";
import { Link } from "@/components/shell";
import { Button } from "@/components/ui/button";
import type { ApiError } from "@/lib/api/errors";
import type { PortfolioEntry, PortfolioResponse } from "@/lib/api/schema";

import { LoadingRows } from "./loading-rows";

/** A market is settled when its `status` is RESOLVED or VOID (DESIGN.md §13). */
function isSettled(entry: PortfolioEntry): boolean {
  return entry.market.status === "RESOLVED" || entry.market.status === "VOID";
}

/**
 * The Positions tab (DESIGN.md §13): the rows of `GET /me/positions` under the
 * subheadings "Open" and "Settled", each group in the order the API sent it. A row names
 * its holdings and shows what the position is worth, or what a settled market paid out
 * or refunded, and opens its market.
 *
 * The read belongs to the screen, which also shows its total; this tab renders its
 * loading, empty and error states.
 */
export function PositionsTab({
  positions,
}: {
  positions: {
    data: PortfolioResponse | undefined;
    error: ApiError | null;
    refetch: () => unknown;
  };
}) {
  return (
    <QueryState query={positions} skeleton={<LoadingRows />}>
      {({ items }) => {
        if (items.length === 0) {
          return (
            <EmptyState
              illustration="empty-cup"
              action={
                <Button asChild>
                  <Link href="/">Browse markets</Link>
                </Button>
              }
            >
              No positions yet. Your cup is empty.
            </EmptyState>
          );
        }

        const open: PortfolioEntry[] = [];
        const settled: PortfolioEntry[] = [];
        for (const entry of items) (isSettled(entry) ? settled : open).push(entry);

        return (
          <div className="flex flex-col gap-6">
            <PositionGroup title="Open" entries={open} />
            <PositionGroup title="Settled" entries={settled} />
          </div>
        );
      }}
    </QueryState>
  );
}

/** One subheading and its rows. A group without rows is left out, heading and all. */
function PositionGroup({ title, entries }: { title: string; entries: readonly PortfolioEntry[] }) {
  const headingId = useId();
  if (entries.length === 0) return null;

  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-1">
      <h2 id={headingId} className="type-h3">
        {title}
      </h2>
      <RowList>
        {entries.map((entry) => (
          <PositionRow key={entry.market.id} entry={entry} />
        ))}
      </RowList>
    </section>
  );
}
