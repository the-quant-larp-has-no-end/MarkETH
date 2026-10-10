"use client";

import Image from "next/image";
import { QRCodeSVG } from "qrcode.react";
import { memo } from "react";

import { Logo } from "@/components/brand";
import { CodeText } from "@/components/money";
import { DateTime } from "@/components/time";
import type { CodeBatchItem, CreatedCode } from "@/lib/api/schema";
import { QR_QUIET_MODULES } from "@/lib/design";
import { formatAmount, formatCode, formatTokens } from "@/lib/format";
import { cn } from "@/lib/utils";

/** DESIGN.md §13: three cards to a row. */
const PER_ROW = 3;

/**
 * What the browser is told while the sheet is laid out: the page is A4, upright, with
 * 12 mm margins, and the sheet is all that reaches the paper. Everything else that stands
 * in `body` (the app with its top bar, tabs and tables, the dialog and its scrim, the
 * toasts) is taken out of a print, so the sheet has to be a child of `body` itself.
 *
 * The rules stand in the sheet and live as long as it does: a print of any other screen
 * is as the browser makes it.
 */
const PAPER = `
@page { size: A4 portrait; margin: 12mm; }
@media print {
  body > :not([data-slot="print-sheet"]) { display: none !important; }
}
`;

/** The codes in rows of three, in their order. */
function inRows(codes: readonly CreatedCode[]): CreatedCode[][] {
  const rows: CreatedCode[][] = [];
  for (let start = 0; start < codes.length; start += PER_ROW) {
    rows.push(codes.slice(start, start + PER_ROW));
  }
  return rows;
}

/**
 * What a code is for, as paper shows it: the bean and the figure. The bean stands without
 * its disc here: the disc is a fill, and a sheet with no fill does not depend on whether
 * the browser prints backgrounds. The image is asked for at once, not when it scrolls
 * into view: a sheet is never in view before it is printed.
 */
function PaperAmount({ value }: { value: string }) {
  return (
    <span data-slot="amount" className="inline-flex items-center gap-1 type-number">
      <Image
        src="/brand/token.png"
        alt=""
        width={256}
        height={256}
        loading="eager"
        className="size-4 pixelated"
      />
      <span aria-hidden>{formatAmount(value)}</span>
      <span className="sr-only">{formatTokens(value)}</span>
    </span>
  );
}

/**
 * The printable QR sheet of a batch (DESIGN.md §13): the one light surface of the
 * product, because it is paper. `--ink` on unprinted paper and no fill anywhere. At the
 * top the logo with name in its dark form, 10 mm tall, and at the right the batch's label
 * and until when its codes are valid. Under it the cards, three to a row and 6 mm apart,
 * in the order of the codes: the QR of the code's `redeem_url`, 40 mm wide with a quiet
 * zone of four modules, the code, and what it is for. Dashed hairlines between the cards
 * show where to cut. A row is never divided between two pages.
 *
 * It exists only on paper: on screen it is not shown. With `codes` it is laid out, and
 * from then on it is all a print of the page shows; without them only its heading is in
 * the page, so that the logo is loaded by the time the cards are, and a print shows the
 * page as it is. Render it into `document.body`.
 *
 * The sheet is 186 mm wide, which is what A4 leaves between 12 mm margins: a card is then
 * 58 mm wide. On paper with less room the cards share what there is.
 */
export const PrintSheet = memo(function PrintSheet({
  batch,
  codes,
}: {
  batch: CodeBatchItem;
  /** The codes to lay out, or null while the sheet has not been asked for. */
  codes: readonly CreatedCode[] | null;
}) {
  const rows = codes === null ? [] : inRows(codes);
  const laidOut = rows.length > 0;

  return (
    <section
      data-slot="print-sheet"
      className={cn("mx-auto hidden w-[186mm] max-w-full text-ink", laidOut && "print:block")}
    >
      {laidOut && <style>{PAPER}</style>}
      <header className="mb-[6mm] flex items-center justify-between gap-[6mm]">
        <Logo on="paper" className="h-[10mm]" />
        <p className="min-w-0 text-right type-body-sm">
          <span className="block wrap-break-word">{batch.label}</span>{" "}
          <span className="block">
            Valid until <DateTime value={batch.expires_at} />
          </span>
        </p>
      </header>
      {rows.map((row, index) => (
        <div
          key={row[0]?.code}
          data-slot="print-row"
          className={cn(
            "grid break-inside-avoid grid-cols-3 gap-[6mm]",
            // Where to cut between two rows: a line in the middle of the 6 mm between them.
            index > 0 && "mt-[3mm] border-t border-dashed border-ink pt-[3mm]",
          )}
        >
          {row.map(({ code, redeem_url }, column) => (
            <div
              key={code}
              data-slot="print-card"
              className={cn(
                "relative flex min-w-0 flex-col items-center gap-[2mm]",
                // Where to cut between two cards of a row: a line in the middle of the
                // 6 mm between them, which runs on to the lines above and below the row.
                column > 0 &&
                  "before:absolute before:-top-[3mm] before:-bottom-[3mm] before:-left-[3mm] before:border-l before:border-dashed before:border-ink",
              )}
            >
              <QRCodeSVG
                value={redeem_url}
                level="M"
                marginSize={QR_QUIET_MODULES}
                bgColor="transparent"
                fgColor="currentColor"
                role="img"
                aria-label={`QR code of ${formatCode(code)}`}
                className="h-auto w-[40mm] max-w-full"
              />
              <span className="type-body">
                <CodeText value={code} />
              </span>
              <PaperAmount value={batch.value_cr} />
            </div>
          ))}
        </div>
      ))}
    </section>
  );
});
