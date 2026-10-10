"use client";

import { useQuery } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";

import { Banner, QueryState } from "@/components/feedback";
import { Amount } from "@/components/money";
import { Screen } from "@/components/shell";
import { DateTime } from "@/components/time";
import { Button } from "@/components/ui/button";
import { createCodeBatchMutation } from "@/lib/api/mutations";
import { codeBatchesQuery } from "@/lib/api/queries";
import type { CodeBatchItem, CreateCodeBatchRequest } from "@/lib/api/schema";
import { useSubmit } from "@/lib/api/submit";
import { formatCount } from "@/lib/format";

import { AdminTabs } from "./admin-tabs";
import { NewBatchDialog, RevokeDialog } from "./code-dialogs";
import { CodesDialog } from "./codes-dialog";
import { type Column, DataTable, DataTableSkeleton, EmptyLine } from "./data-table";

/** What a row of a batch shows, as `GET /admin/code-batches` sends it. */
const COLUMNS: readonly Column<CodeBatchItem>[] = [
  // The label wraps into the room the figures and the two buttons leave at 1024 px.
  { header: "Label", wide: true, className: "min-w-40", cell: (batch) => batch.label },
  { header: "Tokens per code", numeric: true, cell: (batch) => <Amount value={batch.value_cr} /> },
  { header: "Codes", numeric: true, cell: (batch) => formatCount(batch.count) },
  { header: "Active", numeric: true, cell: (batch) => formatCount(batch.active_count) },
  { header: "Redeemed", numeric: true, cell: (batch) => formatCount(batch.redeemed_count) },
  { header: "Revoked", numeric: true, cell: (batch) => formatCount(batch.revoked_count) },
  { header: "Expires", cell: (batch) => <DateTime value={batch.expires_at} /> },
];

/** The codes dialog of one opening: for a batch, with the codes of its creation or without. */
type Shown = {
  batch: CodeBatchItem;
  /** The dialog shows the codes of the answer that created the batch. */
  created: boolean;
  /** Gives every opening a dialog of its own. */
  round: number;
};

/**
 * Admin: code batches (`/admin/codes`, DESIGN.md §13). The table of batches, "New batch",
 * and "View codes" and "Revoke" on each batch.
 *
 * The codes of a batch are shown in a dialog, with "Download CSV" and "Print QR sheet".
 * It opens by itself with the codes of the answer that created a batch, and "View codes"
 * opens it for any batch with the codes the API says can be redeemed. The codes live in
 * memory while the dialog shows them: once it has closed, the answer that held them is
 * let go of. Nothing here names a price for a code.
 *
 * A creation without an answer may or may not have happened: the table is read again
 * before anything else, and a batch that arrived is in it, with its codes behind "View
 * codes".
 */
export function AdminCodesScreen() {
  const batches = useQuery(codeBatchesQuery);
  // Its answer holds the codes of the batch it created. `gcTime: 0`: once the screen lets
  // go of the answer, nothing keeps it.
  const create = useSubmit({ ...createCodeBatchMutation, gcTime: 0 });

  // `round` gives the form a fresh start after each batch that was created. The three
  // dialogs stand side by side, so each names its kind in its key.
  const [form, setForm] = useState({ open: false, round: 0 });
  const [revoking, setRevoking] = useState<{ batch: CodeBatchItem; round: number } | null>(null);
  const [revokeOpen, setRevokeOpen] = useState(false);
  const [shown, setShown] = useState<Shown | null>(null);
  const [shownOpen, setShownOpen] = useState(false);

  const headingId = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const opener = useRef<HTMLButtonElement | null>(null);

  /**
   * Gives the focus back to what opened a dialog (DESIGN.md §15). When that is gone or
   * cannot be used, the focus goes to the heading of the table.
   */
  const returnFocus = (event: Event) => {
    event.preventDefault();
    const from = opener.current;
    const back = from !== null && from.isConnected && !from.matches(":disabled");
    (back ? from : heading.current)?.focus();
  };

  const showCodes = (batch: CodeBatchItem, created: boolean) => {
    setShown((last) => ({ batch, created, round: (last?.round ?? 0) + 1 }));
    setShownOpen(true);
  };

  const submit = (batch: CreateCodeBatchRequest) => {
    // A second submit sends nothing while the first is out (DESIGN.md §14).
    create.mutate(batch, {
      onSuccess: (created) => {
        setForm(({ round }) => ({ open: false, round: round + 1 }));
        showCodes(created.batch, true);
      },
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
      cell: (batch) => (
        <span className="flex justify-end gap-2">
          <Button
            variant="secondary"
            size="compact"
            width="fit"
            aria-label={`View codes of ${batch.label}`}
            onClick={(event) => {
              opener.current = event.currentTarget;
              showCodes(batch, false);
            }}
          >
            View codes
          </Button>
          <Button
            variant="secondary"
            size="compact"
            width="fit"
            aria-label={`Revoke ${batch.label}`}
            // The server says whether a code is left that a revocation would stop.
            disabled={!batch.can_revoke}
            onClick={(event) => {
              opener.current = event.currentTarget;
              setRevoking((last) => ({ batch, round: (last?.round ?? 0) + 1 }));
              setRevokeOpen(true);
            }}
          >
            Revoke
          </Button>
        </span>
      ),
    },
  ];

  const unanswered = create.error?.outcomeUnknown === true && !form.open;

  return (
    <Screen top="main" title="Codes">
      <AdminTabs />

      {unanswered && (
        <Banner>
          We did not get an answer. If the batch is in the table below, &quot;View codes&quot; shows
          its codes.
        </Banner>
      )}

      <section aria-labelledby={headingId} className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 id={headingId} ref={heading} tabIndex={-1} className="type-h3">
            Batches
          </h2>
          <Button
            width="fit"
            disabled={create.isPending}
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

      <NewBatchDialog
        key={`form-${form.round}`}
        open={form.open}
        onClose={() => setForm(({ round }) => ({ open: false, round }))}
        onCloseAutoFocus={(event) => {
          // The codes of the batch it created have opened in its place and hold the focus.
          if (shownOpen) event.preventDefault();
          else returnFocus(event);
        }}
        pending={create.isPending}
        error={create.error}
        onSubmit={submit}
        onEdited={() => {
          if (create.isError) create.reset();
        }}
      />
      {revoking !== null && (
        <RevokeDialog
          key={`revoke-${revoking.round}`}
          batch={revoking.batch}
          open={revokeOpen}
          onClose={() => setRevokeOpen(false)}
          onCloseAutoFocus={returnFocus}
        />
      )}
      {shown !== null && (
        <CodesDialog
          key={`codes-${shown.round}`}
          batch={shown.batch}
          created={shown.created ? create.data : undefined}
          open={shownOpen}
          onClose={() => setShownOpen(false)}
          onCloseAutoFocus={(event) => {
            returnFocus(event);
            // The dialog has gone, and the codes go with it: the answer that created the
            // batch is the one place that held them.
            if (shown.created && !create.isPending) create.reset();
          }}
        />
      )}
    </Screen>
  );
}
