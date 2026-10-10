"use client";

import { useQuery } from "@tanstack/react-query";
import { DownloadIcon, PrinterIcon } from "lucide-react";
import { type ReactNode, useDeferredValue, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { ErrorBanner } from "@/components/feedback";
import { CodeText } from "@/components/money";
import { SheetBody, SheetFooter, sheetHeaderClasses } from "@/components/sheet";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import type { ApiError } from "@/lib/api/errors";
import { batchCodesQuery } from "@/lib/api/queries";
import type { CodeBatchItem } from "@/lib/api/schema";
import { formatCount } from "@/lib/format";
import { cn } from "@/lib/utils";

import type { DialogFrame } from "./action-dialog";
import { browser } from "./browser";
import { type BatchCodes, codesCsv, csvFileName } from "./codes-csv";
import { PrintSheet } from "./print-sheet";

/** What the dialog says of a batch that has no code left to show. */
const NONE_LEFT = "Every code of this batch is redeemed or revoked.";
/** The same for a batch whose time has passed: the API lists none of its codes. */
const EXPIRED = "The codes of this batch have expired.";

/**
 * What every state of the dialog shares, top to bottom: the line that says how many codes
 * it shows, the two buttons, what the dialog has to show, and "Done" under the line of
 * the footer.
 */
function Frame({
  line,
  onSave,
  print,
  children,
  onDone,
}: {
  /**
   * How many codes the dialog shows. Null while they are on their way; left out when it
   * has none to count.
   */
  line?: string | null;
  /** Saves the CSV; without it "Download CSV" is disabled. */
  onSave?: () => void;
  /** Prints the sheet; `waiting` while the sheet is laid out, and without it disabled. */
  print?: { waiting: boolean; onPrint: () => void };
  children: ReactNode;
  onDone: () => void;
}) {
  return (
    <>
      <SheetBody>
        {line === null ? (
          <Skeleton className="h-5 w-20" />
        ) : (
          line !== undefined && <p className="type-body-sm text-muted-foreground">{line}</p>
        )}
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          <Button variant="secondary" width="full" disabled={onSave === undefined} onClick={onSave}>
            <DownloadIcon aria-hidden />
            Download CSV
          </Button>
          <Button
            variant="secondary"
            width="full"
            disabled={print === undefined}
            loading={print?.waiting}
            onClick={print?.onPrint}
          >
            <PrinterIcon aria-hidden />
            Print QR sheet
          </Button>
        </div>
        {children}
      </SheetBody>
      <SheetFooter>
        <Button width="full" onClick={onDone}>
          Done
        </Button>
      </SheetFooter>
    </>
  );
}

/**
 * The codes of a batch, with what is made from them in the browser: the CSV file and the
 * QR sheet. It is one frame for every state: while the codes are on their way (`shown` is
 * not there yet) the box holds skeleton lines and the two buttons are disabled, and a
 * failure shows the banner with "Try again". The buttons and "Done" stay the elements they
 * are when the codes arrive, so the focus stays where it was.
 *
 * The sheet is laid out as soon as the codes are there, behind the dialog's first picture
 * and without holding the page, and "Print QR sheet" waits for it. A tap on the button
 * then finds the sheet on the page and asks the browser for its print dialog in the tap
 * itself: nothing is rendered, awaited or scheduled between the tap and `print()`.
 */
function Codes({
  shown,
  failure = null,
  onRetry,
  recorded,
  onDone,
}: {
  /** The batch and its codes, or undefined while they are read. */
  shown: BatchCodes | undefined;
  /** Why the codes could not be read. */
  failure?: ApiError | null;
  onRetry?: () => void;
  /** The codes were read with "View codes", which the API writes to its audit log. */
  recorded: boolean;
  onDone: () => void;
}) {
  const none = shown === undefined || shown.codes.length === 0;
  // Null in the dialog's first picture; the codes once the sheet is laid out with them.
  const sheet = useDeferredValue<BatchCodes | null>(shown ?? null, null);
  const laidOut = shown !== undefined && sheet === shown;

  /** What lets go of each file that was offered for saving. */
  const files = useRef<(() => void)[]>([]);
  useEffect(() => {
    const offered = files.current;
    return () => {
      // The codes go with the dialog: nothing of them stays behind in the page.
      for (const release of offered.splice(0)) release();
    };
  }, []);

  const save = () => {
    if (shown === undefined) return;
    files.current.push(
      browser.saveFile(csvFileName(shown.batch.label), codesCsv(shown), "text/csv;charset=utf-8"),
    );
  };

  /** The line that says how many codes the dialog shows: null while they are read. */
  const line =
    shown === undefined
      ? failure === null
        ? null
        : undefined
      : shown.codes.length === 0
        ? shown.batch.expired
          ? EXPIRED
          : NONE_LEFT
        : shown.codes.length === 1
          ? "1 code"
          : `${formatCount(shown.codes.length)} codes`;

  return (
    <>
      <Frame
        line={line}
        onSave={none ? undefined : save}
        print={
          none
            ? undefined
            : {
                waiting: !laidOut,
                onPrint: () => {
                  // The button is enabled by the commit that put the sheet on the page.
                  if (laidOut) browser.print();
                },
              }
        }
        onDone={onDone}
      >
        {shown === undefined ? (
          failure !== null ? (
            <ErrorBanner error={failure} onRetry={onRetry} />
          ) : (
            <div
              data-slot="codes-skeleton"
              role="status"
              className="flex flex-col gap-3 rounded-md bg-background px-3 py-3"
            >
              <span className="sr-only">Loading</span>
              {Array.from({ length: 5 }, (_, index) => (
                <Skeleton key={index} className="h-5 w-40" />
              ))}
            </div>
          )
        ) : (
          <>
            {!none && (
              <ul
                aria-label="Codes"
                // The list scrolls in its own box, which the keyboard has to reach to scroll it.
                tabIndex={0}
                className="grid max-h-64 grid-cols-[repeat(auto-fill,minmax(10.5rem,1fr))] gap-x-6 overflow-y-auto rounded-md bg-background px-3 py-2"
              >
                {shown.codes.map(({ code }) => (
                  <li key={code} className="py-1 type-body">
                    <CodeText value={code} />
                  </li>
                ))}
              </ul>
            )}
            {recorded && (
              <p className="type-caption text-muted-foreground">Viewing codes is recorded.</p>
            )}
          </>
        )}
      </Frame>
      {shown !== undefined &&
        createPortal(
          <PrintSheet batch={shown.batch} codes={laidOut && !none ? shown.codes : null} />,
          document.body,
        )}
    </>
  );
}

/**
 * The codes of a batch that can be redeemed, read with `GET
 * /admin/code-batches/{batch_id}/codes`: once per opening of the dialog, and not again
 * behind the admin's back, because the API records every read.
 */
function ReadCodes({ batchId, onDone }: { batchId: string; onDone: () => void }) {
  // One opening is one read, and one row in the audit log. The read is asked for once the
  // dialog stands, not while React is still mounting it: in development it mounts every
  // component twice, and a read that started with the first mount would be sent again.
  const standing = useDeferredValue(true, false);
  const read = useQuery({ ...batchCodesQuery(batchId), enabled: standing });

  return (
    <Codes
      shown={read.data}
      failure={read.error}
      onRetry={() => void read.refetch()}
      recorded
      onDone={onDone}
    />
  );
}

/**
 * The codes of a batch (DESIGN.md §13): a dialog titled with the batch's label. One line
 * says how many codes it shows; "Download CSV" and "Print QR sheet" are made in the
 * browser from them; the codes stand in a box that scrolls; "Done" and the close cross
 * both close it.
 *
 * It opens by itself with the `created` codes of the answer that made a batch. Without
 * them it is "View codes": it asks the API for the codes of the batch that can be
 * redeemed. Either way the codes live in memory while the dialog shows them, and are
 * written to no browser storage; closing it forgets them, and "View codes" brings them
 * back.
 */
export function CodesDialog({
  open,
  onClose,
  onCloseAutoFocus,
  batch,
  created,
}: DialogFrame & {
  /** The batch whose codes are shown: its label is the title from the first moment. */
  batch: CodeBatchItem;
  /** The batch and its codes as the answer that created it holds them. */
  created?: BatchCodes;
}) {
  // What an opening shows is settled when it opens: a dialog for the codes of a creation
  // never turns into a read of the API, also after its answer has been let go of.
  const [ofCreation] = useState(created !== undefined);

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <DialogContent
        className={cn("gap-0 p-0", sheetHeaderClasses)}
        onCloseAutoFocus={onCloseAutoFocus}
        aria-describedby={undefined}
      >
        <DialogHeader className="px-4 pt-4 pb-2">
          <DialogTitle className="wrap-break-word">{batch.label}</DialogTitle>
        </DialogHeader>
        {ofCreation ? (
          created !== undefined && <Codes shown={created} recorded={false} onDone={onClose} />
        ) : (
          <ReadCodes batchId={batch.id} onDone={onClose} />
        )}
      </DialogContent>
    </Dialog>
  );
}
