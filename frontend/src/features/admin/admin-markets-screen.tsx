"use client";

import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useId, useState } from "react";

import { QueryState } from "@/components/feedback";
import { Field } from "@/components/form";
import { StatusBadge, statusName, useAskAtClose } from "@/components/market";
import { Amount } from "@/components/money";
import { Screen } from "@/components/shell";
import { DateTime } from "@/components/time";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import type { ApiError } from "@/lib/api/errors";
import { marketsQuery } from "@/lib/api/queries";
import type { MarketListResponse, MarketStatus, MarketSummary } from "@/lib/api/schema";

import { AdminTabs } from "./admin-tabs";
import { Chance, type Column, DataTable, DataTableSkeleton, EmptyLine } from "./data-table";
import { ALL_STATUSES } from "./words";

const ALL = "ALL";
const PROPOSED = ["PROPOSED"] as const satisfies readonly MarketStatus[];

/** What a row of a market shows: the question, the status badge, `p_yes`, `volume_cr`, `close_at`. */
const COLUMNS: readonly Column<MarketSummary>[] = [
  { header: "Question", wide: true, cell: (market) => market.question },
  {
    header: "Status",
    cell: (market) => <StatusBadge status={market.status} outcome={market.outcome} />,
  },
  { header: "Chance", numeric: true, cell: (market) => <Chance value={market.p_yes} /> },
  {
    header: "Volume",
    numeric: true,
    cell: (market) => <Amount value={market.volume_cr} />,
  },
  { header: "Closes", cell: (market) => <DateTime value={market.close_at} /> },
];

function Markets({
  query,
  label,
  empty,
}: {
  query: { data: MarketListResponse | undefined; error: ApiError | null; refetch: () => unknown };
  label: string;
  empty: string;
}) {
  return (
    <QueryState query={query} skeleton={<DataTableSkeleton columns={COLUMNS} />}>
      {({ items }) =>
        items.length === 0 ? (
          <EmptyLine>{empty}</EmptyLine>
        ) : (
          <DataTable
            label={label}
            columns={COLUMNS}
            rows={items}
            rowKey={(market) => market.id}
            rowHref={(market) => `/admin/markets/${market.id}`}
          />
        )
      }
    </QueryState>
  );
}

function Section({
  title,
  controls,
  children,
}: {
  title: string;
  /** What sits at the end of the heading's row. */
  controls?: ReactNode;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="flex flex-col gap-2">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h2 id={id} className="type-h3">
          {title}
        </h2>
        {controls}
      </div>
      {children}
    </section>
  );
}

/**
 * Admin: the proposal queue and the markets (`/admin`, DESIGN.md §13). First the
 * proposals that wait for a decision, from `GET /markets` with `status` PROPOSED: the
 * API lists there what an admin can still decide on, and a proposal whose close time has
 * passed is reported EXPIRED and is not among them. Then all markets, with a status
 * select. Each choice of the select sends its one `status`, and "All" sends every status
 * by name, because a request without `status` leaves PROPOSED, EXPIRED and REJECTED out.
 * A row opens the admin view of its market.
 *
 * The rows stand in the API's order. The lists are read when the screen opens and when the
 * window gets the focus back. One thing happens to them without anybody acting: a proposal
 * in review reaches its `close_at` and expires, and no event says so. So both lists are
 * read again at the earliest close time among the rows they show as PROPOSED, and after
 * the answer at the earliest one that is left (`useAskAtClose`): the proposal leaves the
 * queue, and the table shows it as the API reports it.
 */
export function AdminMarketsScreen() {
  const [status, setStatus] = useState<MarketStatus | typeof ALL>(ALL);
  const proposals = useQuery(marketsQuery(PROPOSED));
  const markets = useQuery(marketsQuery(status === ALL ? ALL_STATUSES : [status]));
  const { refetch: readProposals } = proposals;
  const { refetch: readMarkets } = markets;

  const closeTimes = [...(proposals.data?.items ?? []), ...(markets.data?.items ?? [])]
    .filter((market) => market.status === "PROPOSED")
    .map((market) => market.close_at);
  useAskAtClose(closeTimes, () => {
    void readProposals({ cancelRefetch: false });
    void readMarkets({ cancelRefetch: false });
  });

  return (
    <Screen top="main" title="Admin">
      <AdminTabs />
      <Section title="Proposals">
        <Markets query={proposals} label="Proposals" empty="No proposals waiting." />
      </Section>
      <Section
        title="Markets"
        controls={
          <Field label="Status" className="w-full md:w-56">
            {(control) => (
              <NativeSelect
                {...control}
                name="status"
                value={status}
                onChange={(event) => setStatus(event.target.value as MarketStatus | typeof ALL)}
              >
                <NativeSelectOption value={ALL}>All</NativeSelectOption>
                {ALL_STATUSES.map((entry) => (
                  <NativeSelectOption key={entry} value={entry}>
                    {statusName(entry)}
                  </NativeSelectOption>
                ))}
              </NativeSelect>
            )}
          </Field>
        }
      >
        <Markets query={markets} label="Markets" empty="No markets." />
      </Section>
    </Screen>
  );
}
