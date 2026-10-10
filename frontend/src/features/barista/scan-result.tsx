"use client";

import { CheckIcon, XIcon } from "lucide-react";

import { Amount } from "@/components/money";
import type { VoucherRedeemResponse } from "@/lib/api/schema";
import { cn } from "@/lib/utils";

/** What the API said of a voucher. A rejection carries no reason (SPEC A.6). */
export type Verdict = { accepted: true; voucher: VoucherRedeemResponse } | { accepted: false };

/* The one full-bleed green or red surface of the product: over everything, the bars and a
   sheet that is still sliding away included. */
const surface =
  "fixed inset-0 z-60 flex flex-col items-center justify-center gap-4 px-6 py-20 text-center text-ink";

/**
 * The result of a scan (DESIGN.md §13), readable from across a counter.
 *
 * - **Accepted:** `--yes`, a large check, the product in `display`, "Voucher accepted",
 *   what the voucher took, and the line of the bar at the bottom. The product is the
 *   largest thing on the screen: it is what the barista hands over.
 * - **Rejected:** `--no`, a large cross and "Voucher not valid". Nothing says why.
 *
 * Text on both fills is `--ink`, never white. The element is always in the page and is
 * read out the moment a result appears in it.
 */
export function ScanResult({ verdict }: { verdict: Verdict | null }) {
  return (
    <div data-slot="scan-result" aria-live="assertive" aria-atomic="true">
      {verdict === null ? null : verdict.accepted ? (
        <div data-verdict="accepted" className={cn(surface, "bg-yes")}>
          <CheckIcon aria-hidden className="size-24 shrink-0" />
          <p className="max-w-full type-display wrap-break-word">{verdict.voucher.product_name}</p>
          <p className="type-h2">Voucher accepted</p>
          <Amount value={verdict.voucher.value_cr} size="lg" />
          <p className="absolute inset-x-6 bottom-[max(1.5rem,env(safe-area-inset-bottom))] type-body">
            Paid in predictions.
          </p>
        </div>
      ) : (
        <div data-verdict="rejected" className={cn(surface, "bg-no")}>
          <XIcon aria-hidden className="size-24 shrink-0" />
          <p className="type-display">Voucher not valid</p>
        </div>
      )}
    </div>
  );
}
