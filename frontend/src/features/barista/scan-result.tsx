"use client";

import { CheckIcon, ClockIcon, XIcon } from "lucide-react";

import { Amount } from "@/components/money";
import type { VoucherRedeemResponse } from "@/lib/api/schema";
import { useConfig } from "@/lib/api/session";
import { formatTime } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * What the API said of a voucher. `accepted` is the scan that redeemed it. `repeat` is a
 * scan of a voucher this barista redeemed a moment ago: the API's answer says which of the
 * two it is, in its `repeat` field. A rejection carries no reason (SPEC A.6).
 */
export type Verdict =
  | { kind: "accepted"; voucher: VoucherRedeemResponse }
  | { kind: "repeat"; voucher: VoucherRedeemResponse }
  | { kind: "rejected" };

/** Tells which of the two a redemption the API answered with is. */
export function verdictOf(voucher: VoucherRedeemResponse): Verdict {
  return voucher.repeat ? { kind: "repeat", voucher } : { kind: "accepted", voucher };
}

/* The full-bleed surface of a result: over everything, the bars and a sheet that is still
   sliding away included. */
const surface =
  "fixed inset-0 z-60 flex flex-col items-center justify-center gap-4 px-6 py-20 text-center";
const footnote =
  "absolute inset-x-6 bottom-[max(1.5rem,env(safe-area-inset-bottom))] type-body text-balance";

/**
 * The result of a scan (DESIGN.md §13), readable from across a counter.
 *
 * - **Accepted:** `--yes`, a large check, the product in `display`, "Voucher accepted",
 *   what the voucher took, and the line of the bar at the bottom. The product is the
 *   largest thing on the screen: it is what the barista hands over.
 * - **Rejected:** `--no`, a large cross and "Voucher not valid". Nothing says why.
 * - **Redeemed a moment ago:** the voucher is one this barista redeemed a moment ago,
 *   scanned again: the answer to the first scan may not have arrived, or the same QR came
 *   under the camera twice. `--raised` with `--foreground` text, neither green nor red: it
 *   is no second voucher and no refusal. A large clock, the product in `display`, the
 *   sentence with the time of day of the redemption, what the voucher took, and at the
 *   bottom what to do. The barista hands the product out once.
 *
 * Text on the green and the red fill is `--ink`, never white. The element is always in the
 * page and is read out the moment a result appears in it, the time of a repeat included.
 */
export function ScanResult({ verdict }: { verdict: Verdict | null }) {
  const { timezone } = useConfig();

  return (
    <div data-slot="scan-result" aria-live="assertive" aria-atomic="true">
      {verdict === null ? null : verdict.kind === "accepted" ? (
        <div data-verdict="accepted" className={cn(surface, "bg-yes text-ink")}>
          <CheckIcon aria-hidden className="size-24 shrink-0" />
          <p className="max-w-full type-display wrap-break-word">{verdict.voucher.product_name}</p>
          <p className="type-h2">Voucher accepted</p>
          <Amount value={verdict.voucher.value_cr} size="lg" />
          <p className={footnote}>Paid in predictions.</p>
        </div>
      ) : verdict.kind === "repeat" ? (
        <div data-verdict="repeat" className={cn(surface, "bg-raised text-foreground")}>
          <ClockIcon aria-hidden className="size-24 shrink-0" />
          <p className="max-w-full type-display wrap-break-word">{verdict.voucher.product_name}</p>
          <p className="flex flex-col items-center gap-1">
            <span className="type-h2 text-balance">Redeemed a moment ago at this bar</span>{" "}
            <time dateTime={verdict.voucher.redeemed_at} className="type-number-lg">
              {formatTime(verdict.voucher.redeemed_at, timezone)}
            </time>
          </p>
          <Amount value={verdict.voucher.value_cr} />
          <p className={footnote}>It counts once. Hand it over if you have not yet.</p>
        </div>
      ) : (
        <div data-verdict="rejected" className={cn(surface, "bg-no text-ink")}>
          <XIcon aria-hidden className="size-24 shrink-0" />
          <p className="type-display">Voucher not valid</p>
        </div>
      )}
    </div>
  );
}
