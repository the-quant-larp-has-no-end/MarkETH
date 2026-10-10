"use client";

import { Logo } from "@/components/brand";
import { Amount } from "@/components/money";
import { QrCode } from "@/components/qr";
import { DateTime } from "@/components/time";
import type { CodeBatchCreatedResponse } from "@/lib/api/schema";
import { formatCode } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * The printable QR sheet of a new batch (DESIGN.md §13): the one light surface of the
 * product, because it is paper. `--ink` on unprinted paper, the logo with name in its
 * dark form at the top, then a grid of QR codes with the code and its value under each.
 * Each QR holds the `redeem_url` of its code, which opens the Wallet with the code filled
 * in.
 *
 * It exists only on paper: on screen it is not shown, and once it is `laidOut` it is the
 * only thing a print of the page shows. A card is never cut in two by a page break.
 *
 * Until it is asked for, only its logo is in the page, so that the image is there when
 * the print dialog opens; the cards, one QR per code, are laid out with `laidOut`.
 */
export function PrintSheet({
  created,
  laidOut,
}: {
  created: CodeBatchCreatedResponse;
  /** The sheet was asked for: its cards are in the page and paper shows it. */
  laidOut: boolean;
}) {
  const { batch, codes } = created;

  return (
    <section data-slot="print-sheet" className={cn("hidden text-ink", laidOut && "print:block")}>
      <header className="mb-6 flex items-center justify-between gap-6">
        <Logo on="paper" size="large" />
        <p className="text-right type-body-sm">
          {batch.label}
          <br />
          Valid until <DateTime value={batch.expires_at} />
        </p>
      </header>
      {laidOut && (
        // Cards of the smallest size a QR may have, so that a page holds as many as it can.
        <ul className="grid grid-cols-[repeat(auto-fill,15rem)] justify-around gap-x-4 gap-y-6">
          {codes.map(({ code, redeem_url }) => (
            <li key={code} className="flex w-60 break-inside-avoid flex-col items-center gap-2">
              <QrCode value={redeem_url} label={`QR code of ${formatCode(code)}`} code={code} />
              <Amount value={batch.value_cr} size="lg" />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
