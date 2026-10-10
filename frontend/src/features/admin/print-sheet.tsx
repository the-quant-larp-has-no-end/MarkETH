"use client";

import { QRCodeSVG } from "qrcode.react";
import { memo } from "react";

import { Logo } from "@/components/brand";
import { Amount, CodeText } from "@/components/money";
import { DateTime } from "@/components/time";
import type { CodeBatchItem, CreatedCode } from "@/lib/api/schema";
import { QR_QUIET_MODULES } from "@/lib/design";
import { formatCode } from "@/lib/format";
import { cn } from "@/lib/utils";

/** DESIGN.md §13: three cards to a row, also in a row that holds fewer. */
const PER_ROW = 3;

/**
 * What the browser is told while the sheet is laid out: the page is A4, upright, with
 * 12 mm margins, and the sheet is all that reaches the paper. Everything else that stands
 * in `body` (the app with its top bar, tabs and tables, the dialog and its scrim, the
 * toasts) is taken out of a print, so the sheet has to be a child of `body` itself.
 *
 * A card is a navy surface, and a browser leaves fills off paper unless it is asked to
 * keep them: the sheet asks, so that a print does not depend on "Background graphics"
 * being ticked in the print dialog.
 *
 * The rules stand in the sheet and live as long as it does: a print of any other screen
 * is as the browser makes it.
 */
const PAPER = `
@page { size: A4 portrait; margin: 12mm; }
@media print {
  body > :not([data-slot="print-sheet"]) { display: none !important; }
  [data-slot="print-sheet"], [data-slot="print-sheet"] * {
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }
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
 * The printable QR sheet of a batch (DESIGN.md §13). The page is paper: at its top the
 * logo with name in its dark form, 10 mm tall, at the right the batch's label (`label`)
 * and, smaller, until when its codes are valid (`caption`), and a hairline in `--ink`
 * under the two.
 *
 * Under it the cards, in three columns of 58 mm that are 6 mm apart, in the order of the
 * codes. A card is a piece of the app on paper, in the app's own colours and built from
 * its own components: a `--card` surface with `--radius-lg`; at its top the logo with
 * name and, at the right, what the code is for as the balance pill of the top bar shows
 * a balance; then the QR of the code's `redeem_url` on its `--foreground` tile, 40 mm
 * wide with a quiet zone of four modules, as every QR of the app; then the code in code
 * style and until when it is valid. A card that is cut out says by itself what it is.
 * Dashed hairlines in the gaps between the cards show where to cut. A row is never
 * divided between two pages.
 *
 * It exists only on paper: on screen it is not shown. With `codes` it is laid out, and
 * from then on it is all a print of the page shows; without them only its heading is in
 * the page, so that the logo is loaded by the time the cards are, and a print shows the
 * page as it is. Render it into `document.body`.
 *
 * The sheet is 186 mm wide, which is what A4 leaves between 12 mm margins, and its
 * columns are 58 mm whatever the row holds: a batch of one or two codes leaves the rest
 * of its row empty.
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
      className={cn("mx-auto hidden w-[186mm] text-ink", laidOut && "print:block")}
    >
      {laidOut && <style>{PAPER}</style>}
      <header className="mb-[6mm] flex items-end justify-between gap-[6mm] border-b border-ink pb-[4mm]">
        <Logo on="paper" className="h-[10mm]" />
        <p className="min-w-0 text-right">
          <span className="block type-label wrap-break-word">{batch.label}</span>{" "}
          <span className="block type-caption">
            Valid until <DateTime value={batch.expires_at} />
          </span>
        </p>
      </header>
      {rows.map((row, index) => (
        <div
          key={row[0]?.code}
          data-slot="print-row"
          className={cn(
            "grid break-inside-avoid grid-cols-[repeat(3,58mm)] gap-[6mm]",
            // Where to cut between two rows: a line in the middle of the 6 mm between them.
            index > 0 && "mt-[3mm] border-t border-dashed border-ink pt-[3mm]",
          )}
        >
          {row.map(({ code, redeem_url }, column) => (
            <div
              key={code}
              data-slot="print-card"
              className={cn(
                "relative flex min-w-0 flex-col gap-[3mm] rounded-lg bg-card p-[3mm] text-foreground",
                // Where to cut between two cards of a row: a line in the middle of the
                // 6 mm between them, which runs on to the lines above and below the row.
                column > 0 &&
                  "before:absolute before:-top-[3mm] before:-bottom-[3mm] before:-left-[3mm] before:border-l before:border-dashed before:border-ink",
              )}
            >
              <div className="flex items-center justify-between gap-[2mm]">
                <Logo className="h-[5mm]" />
                <span
                  data-slot="print-value"
                  className="inline-flex h-[7mm] items-center rounded-full bg-raised px-[2.5mm]"
                >
                  <Amount value={batch.value_cr} className="font-semibold" />
                </span>
              </div>
              <div
                data-slot="print-qr"
                className="w-[40mm] self-center rounded-lg bg-foreground text-ink"
              >
                <QRCodeSVG
                  value={redeem_url}
                  level="M"
                  marginSize={QR_QUIET_MODULES}
                  bgColor="transparent"
                  fgColor="currentColor"
                  role="img"
                  aria-label={`QR code of ${formatCode(code)}`}
                  className="h-auto w-full"
                />
              </div>
              <p className="flex flex-col items-center text-center">
                <CodeText value={code} className="type-number" />{" "}
                <span className="type-caption text-muted-foreground">
                  Valid until <DateTime value={batch.expires_at} />
                </span>
              </p>
            </div>
          ))}
        </div>
      ))}
    </section>
  );
});
