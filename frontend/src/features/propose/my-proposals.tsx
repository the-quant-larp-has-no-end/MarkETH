"use client";

import { useQuery } from "@tanstack/react-query";
import { useId } from "react";

import { EmptyState, QueryState } from "@/components/feedback";
import { StatusBadge } from "@/components/market";
import { rowClasses, RowItem, RowList, rowPressClasses, RowSkeletons } from "@/components/rows";
import { Link } from "@/components/shell";
import { proposalsQuery } from "@/lib/api/queries";
import type { ProposalItem } from "@/lib/api/schema";
import { cn } from "@/lib/utils";

/**
 * One proposal: the question, its status badge and, when it was rejected, the reason
 * VIS gave, and nothing else about the market. The badge of a proposal that was resolved
 * names its outcome ("Resolved YES", "Resolved NO"), which is `outcome` of the row. A
 * proposal that became a market (its `status` is neither PROPOSED nor REJECTED) is a link
 * to that market.
 */
function ProposalRow({ proposal }: { proposal: ProposalItem }) {
  const isMarket = proposal.status !== "PROPOSED" && proposal.status !== "REJECTED";
  const reason = proposal.status === "REJECTED" ? proposal.rejection_reason : null;

  // The blanks between the parts are for a screen reader, which hears a row as one text.
  const content = (
    <>
      <span className="flex min-w-0 flex-col gap-1">
        <span className="type-body wrap-break-word">{proposal.question}</span>
        {reason !== null && reason !== "" && (
          <>
            {" "}
            <span className="type-body-sm wrap-break-word text-muted-foreground">{reason}</span>
          </>
        )}
      </span>{" "}
      <StatusBadge status={proposal.status} outcome={proposal.outcome} className="shrink-0" />
    </>
  );

  return (
    <RowItem>
      {isMarket ? (
        <Link href={`/markets/${proposal.id}`} className={cn(rowClasses, rowPressClasses)}>
          {content}
        </Link>
      ) : (
        <div className={rowClasses}>{content}</div>
      )}
    </RowItem>
  );
}

/**
 * "My proposals" (DESIGN.md §13), from `GET /me/proposals`, in the API's order, which is
 * newest first. With none: the blackboard and "No proposals yet."
 */
export function MyProposals() {
  const proposals = useQuery(proposalsQuery);
  const headingId = useId();

  return (
    <section aria-labelledby={headingId} data-slot="my-proposals" className="flex flex-col gap-2">
      <h2 id={headingId} className="type-h2">
        My proposals
      </h2>
      <QueryState query={proposals} skeleton={<RowSkeletons count={3} />}>
        {({ items }) =>
          items.length === 0 ? (
            <EmptyState illustration="blackboard">No proposals yet.</EmptyState>
          ) : (
            <RowList>
              {items.map((proposal) => (
                <ProposalRow key={proposal.id} proposal={proposal} />
              ))}
            </RowList>
          )
        }
      </QueryState>
    </section>
  );
}
