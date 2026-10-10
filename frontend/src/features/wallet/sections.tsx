"use client";

import type { InfiniteData, UseInfiniteQueryResult, UseQueryResult } from "@tanstack/react-query";
import { type ReactNode, useId } from "react";

import { EmptyState, LoadMore } from "@/components/feedback";
import { LedgerRow, RowList, RowSkeletons, VoucherRow } from "@/components/rows";
import type { ApiError } from "@/lib/api/errors";
import type { LedgerListResponse, VoucherItem, VoucherListResponse } from "@/lib/api/schema";

/** A titled part of the Wallet. The title is an `h2` in the look of a section title. */
function Section({ title, children }: { title: string; children: ReactNode }) {
  const titleId = useId();

  return (
    <section aria-labelledby={titleId} className="flex flex-col gap-2">
      <h2 id={titleId} className="type-h2">
        {title}
      </h2>
      {children}
    </section>
  );
}

/**
 * **Vouchers** (DESIGN.md §13): the rows of `GET /me/vouchers`, in the API's order. An
 * ACTIVE voucher offers "Show QR"; the others say what became of them. With none, one
 * line. While the list is on its way: two skeleton rows.
 *
 * A list that could not be read shows nothing here: the Wallet has one banner for that.
 */
export function VouchersSection({
  vouchers,
  onShowQr,
  askingFor,
  disabled,
}: {
  vouchers: Pick<UseQueryResult<VoucherListResponse, ApiError>, "data" | "error">;
  onShowQr: (voucher: VoucherItem) => void;
  /** The voucher whose fresh code is on its way, if any. */
  askingFor: string | null;
  /** "Show QR" cannot be tapped now. */
  disabled: boolean;
}) {
  const { data, error } = vouchers;
  if (data === undefined && error !== null) return null;

  return (
    <Section title="Vouchers">
      {data === undefined ? (
        <RowSkeletons count={2} />
      ) : data.items.length === 0 ? (
        <p className="type-body text-muted-foreground">No vouchers yet.</p>
      ) : (
        <RowList>
          {data.items.map((voucher) => (
            <VoucherRow
              key={voucher.id}
              voucher={voucher}
              onShowQr={onShowQr}
              loading={askingFor === voucher.id}
              disabled={disabled}
            />
          ))}
        </RowList>
      )}
    </Section>
  );
}

/**
 * **Activity** (DESIGN.md §13): the rows of `GET /me/ledger`, newest first as the API
 * sends them. More rows are asked for with `next_cursor` when the user reaches the end.
 * Without rows: the empty cup and one sentence. While the first page is on its way: five
 * skeleton rows.
 */
export function ActivitySection({
  ledger,
}: {
  ledger: UseInfiniteQueryResult<InfiniteData<LedgerListResponse>, ApiError>;
}) {
  const { data, error } = ledger;
  if (data === undefined && error !== null) return null;
  const items = data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <Section title="Activity">
      {data === undefined ? (
        <RowSkeletons count={5} />
      ) : items.length === 0 ? (
        <EmptyState illustration="empty-cup">Nothing here yet.</EmptyState>
      ) : (
        <>
          <RowList>
            {items.map((item) => (
              <LedgerRow key={item.id} item={item} />
            ))}
          </RowList>
          <LoadMore query={ledger} skeleton={<RowSkeletons count={2} />} />
        </>
      )}
    </Section>
  );
}
