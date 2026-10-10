"use client";

import { useQuery } from "@tanstack/react-query";
import { useId } from "react";

import { EmptyState, QueryState } from "@/components/feedback";
import { isProposal, StatusBadge, useAskAtClose } from "@/components/market";
import { rowClasses, RowItem, RowList, rowPressClasses, RowSkeletons } from "@/components/rows";
import { Link } from "@/components/shell";
import { proposalsQuery } from "@/lib/api/queries";
import type { ProposalItem } from "@/lib/api/schema";
import { cn } from "@/lib/utils";

/** What an expired proposal says under its question. */
const EXPIRED_LINE = "It was not reviewed before its closing time.";

/**
 * One proposal: the question, its status badge and one line under the question where the
 * status has something to add, and nothing else about the market. A rejected proposal
 * shows the reason VIS gave. One whose closing time passed before VIS reviewed it arrives
 * as EXPIRED: its badge reads "Expired", it is in review no more, and the line says so.
 * The badge of a proposal that was resolved names its outcome, which is `outcome` of the
 * row. A proposal that became a market is a link to that market: one in review, one that
 * was not approved and one that expired never did.
 */
function ProposalRow({ proposal }: { proposal: ProposalItem }) {
  const isMarket = !isProposal(proposal.status);
  const note =
    proposal.status === "REJECTED"
      ? proposal.rejection_reason
      : proposal.status === "EXPIRED"
        ? EXPIRED_LINE
        : null;

  // The blanks between the parts are for a screen reader, which hears a row as one text.
  const content = (
    <>
      <span className="flex min-w-0 flex-col gap-1">
        <span className="type-body wrap-break-word">{proposal.question}</span>
        {note !== null && note !== "" && (
          <>
            {" "}
            <span className="type-body-sm wrap-break-word text-muted-foreground">{note}</span>
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
 *
 * The list is read when the screen opens and when the window gets the focus back. One
 * thing happens to it without anybody acting: a proposal in review reaches its `close_at`
 * and expires, and no event says so. So the list is read again at the earliest close time
 * among the rows it shows as PROPOSED, and after the answer at the earliest one that is
 * left (`useAskAtClose`). What a row reads afterwards is the `status` of the answer.
 */
export function MyProposals() {
  const proposals = useQuery(proposalsQuery);
  const { refetch } = proposals;
  const headingId = useId();

  const closeTimes = (proposals.data?.items ?? [])
    .filter((proposal) => proposal.status === "PROPOSED")
    .map((proposal) => proposal.close_at);
  useAskAtClose(closeTimes, () => void refetch({ cancelRefetch: false }));

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
