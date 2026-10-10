"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState, useTransition } from "react";
import { flushSync } from "react-dom";

import { Banner, QueryState } from "@/components/feedback";
import { Amount } from "@/components/money";
import { Screen } from "@/components/shell";
import { DateTime } from "@/components/time";
import { Button } from "@/components/ui/button";
import { createCodeBatchMutation } from "@/lib/api/mutations";
import { codeBatchesQuery } from "@/lib/api/queries";
import type { CodeBatchItem, CreateCodeBatchRequest } from "@/lib/api/schema";
import { formatCount } from "@/lib/format";
import { cn } from "@/lib/utils";

import { AdminTabs } from "./admin-tabs";
import { browser } from "./browser";
import { LeaveCodesDialog, NewBatchDialog, RevokeDialog } from "./code-dialogs";
import { CreatedCodes } from "./created-codes";
import { type Column, DataTable, DataTableSkeleton, EmptyLine } from "./data-table";
import { PrintSheet } from "./print-sheet";
import { useLeaveGuard } from "./use-leave-guard";

/** What a row of a batch shows, as `GET /admin/code-batches` sends it. */
const COLUMNS: readonly Column<CodeBatchItem>[] = [
  { header: "Label", wide: true, cell: (batch) => batch.label },
  { header: "Tokens per code", numeric: true, cell: (batch) => <Amount value={batch.value_cr} /> },
  { header: "Codes", numeric: true, cell: (batch) => formatCount(batch.count) },
  { header: "Active", numeric: true, cell: (batch) => formatCount(batch.active_count) },
  { header: "Redeemed", numeric: true, cell: (batch) => formatCount(batch.redeemed_count) },
  { header: "Revoked", numeric: true, cell: (batch) => formatCount(batch.revoked_count) },
  { header: "Expires", cell: (batch) => <DateTime value={batch.expires_at} /> },
];

/**
 * Admin: code batches (`/admin/codes`, DESIGN.md §13). The table of batches, "New batch",
 * and "Revoke" on each batch.
 *
 * A batch that was just created shows its codes once, above the table, with "Download
 * CSV" and "Print QR sheet". The API sends the plain codes in that one answer and never
 * again, so while they are on screen every way out of the page asks first, and so does
 * "Done". Nothing here names a price for a code.
 *
 * A creation without an answer may or may not have happened: the table is read again,
 * and the screen says what to do with a batch that is in it without its codes.
 */
export function AdminCodesScreen() {
  const batches = useQuery(codeBatchesQuery);
  // Its answer is the one place the plain codes exist. Once the screen lets go of it,
  // nothing keeps it.
  const create = useMutation({ ...createCodeBatchMutation, gcTime: 0 });
  const created = create.data;

  // `round` gives the form a fresh start after each batch that was created.
  const [form, setForm] = useState({ open: false, round: 0 });
  const [revoking, setRevoking] = useState<{ batch: CodeBatchItem; round: number } | null>(null);
  const [revokeOpen, setRevokeOpen] = useState(false);
  // "Done" was tapped and waits for its confirmation: the button that was tapped, or null.
  const [finishing, setFinishing] = useState<HTMLButtonElement | null>(null);
  const guard = useLeaveGuard(created !== undefined);

  // The QR sheet is laid out when it is first asked for, not with every batch: a batch
  // may hold thousands of codes, and most of them leave the page as a file. `print`
  // remembers whose sheet is laid out and counts the times it was sent to the printer.
  const [print, setPrint] = useState<{ batchId: string; runs: number } | null>(null);
  // Laying out a sheet of thousands of cards takes a moment: the button waits, the page does not.
  const [layingOut, startLayingOut] = useTransition();
  const sheetLaidOut = created !== undefined && print?.batchId === created.batch.id;
  const printRuns = sheetLaidOut ? print.runs : 0;
  useEffect(() => {
    // By now the sheet of this run is on the page.
    if (printRuns > 0) browser.print();
  }, [printRuns]);
  // Printing through the browser's own menu means the sheet too: it is laid out in the
  // moment the browser says it is about to print, before it looks at the page.
  const batchId = created?.batch.id;
  useEffect(() => {
    if (batchId === undefined || sheetLaidOut) return;
    const layOut = () => flushSync(() => setPrint({ batchId, runs: 0 }));
    window.addEventListener("beforeprint", layOut);
    return () => window.removeEventListener("beforeprint", layOut);
  }, [batchId, sheetLaidOut]);

  const headingId = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const codesHeading = useRef<HTMLHeadingElement>(null);
  const opener = useRef<HTMLButtonElement | null>(null);

  /**
   * Gives the focus back to what opened a dialog (DESIGN.md §15). When that is gone or
   * cannot be used, the focus goes to what the dialog left behind: the codes of
   * the batch it created, or else the heading of the table.
   */
  const focusAfter = (event: Event, from: HTMLElement | null) => {
    event.preventDefault();
    const back = from !== null && from.isConnected && !from.matches(":disabled");
    (back ? from : (codesHeading.current ?? heading.current))?.focus();
  };
  const returnFocus = (event: Event) => focusAfter(event, opener.current);

  const submit = (batch: CreateCodeBatchRequest) => {
    if (create.isPending) return;
    create.mutate(batch, {
      onSuccess: () => setForm(({ round }) => ({ open: false, round: round + 1 })),
      // Nobody can say whether the batch exists: the table behind the dialog can.
      onError: (error) => {
        if (error.outcomeUnknown) setForm(({ round }) => ({ open: false, round }));
      },
    });
  };

  const columns: readonly Column<CodeBatchItem>[] = [
    ...COLUMNS,
    {
      header: "Actions",
      hideHeader: true,
      cell: (batch) =>
        // `revoked_at` is set once the whole batch was revoked: nothing is left to revoke.
        batch.revoked_at !== null ? (
          <span className="text-muted-foreground">Revoked</span>
        ) : (
          <Button
            variant="secondary"
            size="compact"
            width="fit"
            aria-label={`Revoke ${batch.label}`}
            onClick={(event) => {
              opener.current = event.currentTarget;
              setRevoking((last) => ({ batch, round: (last?.round ?? 0) + 1 }));
              setRevokeOpen(true);
            }}
          >
            Revoke
          </Button>
        ),
    },
  ];

  const unanswered = create.error?.outcomeUnknown === true && !form.open;

  return (
    <Screen top="main" title="Codes">
      {/* Once the QR sheet is laid out, paper shows the sheet and nothing else. */}
      <div className={cn("contents", sheetLaidOut && "print:hidden")}>
        <AdminTabs />

        {created !== undefined && (
          <CreatedCodes
            created={created}
            headingRef={codesHeading}
            printing={layingOut}
            onPrint={() =>
              startLayingOut(() =>
                setPrint((last) => ({
                  batchId: created.batch.id,
                  runs: last?.batchId === created.batch.id ? last.runs + 1 : 1,
                })),
              )
            }
            onDone={setFinishing}
          />
        )}
        {unanswered && (
          <Banner>
            We did not get an answer. If the batch is in the table below, revoke it and create it
            again: its codes cannot be shown.
          </Banner>
        )}

        <section aria-labelledby={headingId} className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 id={headingId} ref={heading} tabIndex={-1} className="type-h3">
              Batches
            </h2>
            <Button
              width="fit"
              // The codes on screen come first: a new batch would take their place.
              disabled={create.isPending || created !== undefined}
              onClick={(event) => {
                opener.current = event.currentTarget;
                if (create.isError) create.reset();
                setForm(({ round }) => ({ open: true, round }));
              }}
            >
              New batch
            </Button>
          </div>
          <QueryState query={batches} skeleton={<DataTableSkeleton columns={columns} />}>
            {({ items }) =>
              items.length === 0 ? (
                <EmptyLine>No batches yet.</EmptyLine>
              ) : (
                <DataTable
                  label="Batches"
                  columns={columns}
                  rows={items}
                  rowKey={(batch) => batch.id}
                />
              )
            }
          </QueryState>
        </section>
      </div>

      {created !== undefined && <PrintSheet created={created} laidOut={sheetLaidOut} />}

      <NewBatchDialog
        key={form.round}
        open={form.open}
        onClose={() => setForm(({ round }) => ({ open: false, round }))}
        onCloseAutoFocus={returnFocus}
        pending={create.isPending}
        error={create.error}
        onSubmit={submit}
        onEdited={() => {
          if (create.isError) create.reset();
        }}
      />
      {revoking !== null && (
        <RevokeDialog
          key={revoking.round}
          batch={revoking.batch}
          open={revokeOpen}
          onClose={() => setRevokeOpen(false)}
          onCloseAutoFocus={returnFocus}
        />
      )}
      <LeaveCodesDialog
        open={finishing !== null || guard.leaving !== null}
        leaving={guard.leaving !== null}
        from={guard.leaving?.by === "link" ? guard.leaving.from : finishing}
        onStay={() => {
          setFinishing(null);
          guard.stay();
        }}
        onLeave={() => {
          if (guard.leaving !== null) {
            guard.leave();
            return;
          }
          setFinishing(null);
          create.reset();
        }}
        onCloseAutoFocus={focusAfter}
      />
    </Screen>
  );
}
