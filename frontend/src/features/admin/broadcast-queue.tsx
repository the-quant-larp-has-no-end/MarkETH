"use client";

import { useQuery } from "@tanstack/react-query";
import { useId } from "react";

import { QueryState } from "@/components/feedback";
import { LiveBadge } from "@/components/market";
import { Missing } from "@/components/money";
import { DateTime } from "@/components/time";
import { adminBroadcastsQuery } from "@/lib/api/queries";
import type { AdminBroadcastItem, BroadcastStatus } from "@/lib/api/schema";
import { liveTopic, useLiveTopic } from "@/lib/live";

import { type Column, DataTable, DataTableSkeleton } from "./data-table";

/**
 * `status` of a live stream is a key, not copy: these are its words in the admin area
 * (DESIGN.md §13). The list holds the first three; the API reports no other there.
 */
const STATUS_NAMES: Record<BroadcastStatus, string> = {
  REQUESTED: "Requested",
  APPROVED: "Approved",
  LIVE: "Live",
  REFUSED: "Not approved",
  ENDED: "Ended",
};

/** A live stream that is on carries the Live badge, which reads "Live" (DESIGN.md §12). */
function Status({ status }: { status: BroadcastStatus }) {
  return status === "LIVE" ? <LiveBadge /> : <>{STATUS_NAMES[status]}</>;
}

/** Who asked: the username, and under it the address of the ETH sign-in. */
function RequestedBy({ row }: { row: AdminBroadcastItem }) {
  if (row.requested_by === null) return <Missing />;
  return (
    <>
      <span className="block">{row.requested_by.username}</span>{" "}
      <span className="block type-caption text-muted-foreground">{row.requested_by.email}</span>
    </>
  );
}

/** The four columns of DESIGN.md §13. The question may wrap; the rest keep their line. */
const COLUMNS: readonly Column<AdminBroadcastItem>[] = [
  { header: "Market", wide: true, cell: (row) => row.question },
  { header: "Status", cell: (row) => <Status status={row.status} /> },
  { header: "Requested by", cell: (row) => <RequestedBy row={row} /> },
  { header: "Requested", cell: (row) => <DateTime value={row.requested_at} /> },
];

/**
 * Admin: the live streams that wait for an admin or are on (`/admin`, DESIGN.md §13). A
 * table headed "Live streams", from `GET /admin/broadcasts`: every live stream that is
 * asked for, approved or on, in the API's order. A row opens the admin view of its
 * market, where the request is decided and a stream is ended.
 *
 * It is there while the answer has rows. With none, the table and its heading are absent
 * and no sentence stands in their place: the screen it stands on has its own tables below
 * it. While the list loads it shows a skeleton row, and a read that failed shows the
 * banner with "Try again": a request may be waiting behind it.
 *
 * It names `markets` to the event stream. A `broadcast.changed` reads the list again
 * (`lib/live/rules.ts`), so a request shows without a reload, and so does every stream
 * that opens and every poll. Nothing here asks a second time.
 */
export function BroadcastQueue() {
  const id = useId();
  const broadcasts = useQuery(adminBroadcastsQuery);
  useLiveTopic(liveTopic.markets());

  if (broadcasts.data?.items.length === 0) return null;

  return (
    <section aria-labelledby={id} data-slot="broadcast-queue" className="flex flex-col gap-2">
      <h2 id={id} className="type-h3">
        Live streams
      </h2>
      <QueryState query={broadcasts} skeleton={<DataTableSkeleton columns={COLUMNS} rows={1} />}>
        {({ items }) => (
          <DataTable
            label="Live streams"
            columns={COLUMNS}
            rows={items}
            rowKey={(row) => row.id}
            rowHref={(row) => `/admin/markets/${row.market_id}`}
          />
        )}
      </QueryState>
    </section>
  );
}
