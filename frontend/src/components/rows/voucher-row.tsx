"use client";

import { DateTime } from "@/components/time";
import { Button } from "@/components/ui/button";
import type { VoucherItem } from "@/lib/api/schema";
import { cn } from "@/lib/utils";

import { rowClasses, RowFigure, RowItem, RowText } from "./list-row";

/**
 * What became of a voucher that can be shown at the bar no more (DESIGN.md §12, §13). An
 * EXPIRED voucher says whether its tokens are back: that is `refunded` of the row, the
 * server's word for whether the refund is booked. No balance is looked at for it.
 */
function fate(voucher: VoucherItem): string {
  if (voucher.status === "REDEEMED") return "Redeemed";
  return voucher.refunded ? "Expired, refunded" : "Expired, refund on its way";
}

/**
 * One voucher of the Wallet (DESIGN.md §12, §13). An ACTIVE voucher shows "Valid until"
 * with `expires_at` and a "Show QR" button, which asks for a fresh code. The others are
 * muted and say what became of them: "Redeemed" with `redeemed_at`, or for an EXPIRED one
 * "Expired, refunded" or "Expired, refund on its way" with `expires_at`. `status` is the
 * API's effective status; the clock is not consulted.
 */
export function VoucherRow({
  voucher,
  onShowQr,
  loading = false,
  disabled = false,
}: {
  voucher: VoucherItem;
  onShowQr: (voucher: VoucherItem) => void;
  /** "Show QR" was tapped for this voucher and its code is on the way. */
  loading?: boolean;
  disabled?: boolean;
}) {
  const active = voucher.status === "ACTIVE";

  return (
    <RowItem>
      <div className={cn(rowClasses, !active && "text-muted-foreground")}>
        <RowText
          title={voucher.product_name}
          below={
            active ? (
              <>
                Valid until <DateTime value={voucher.expires_at} />
              </>
            ) : voucher.status === "REDEEMED" && voucher.redeemed_at !== null ? (
              <DateTime value={voucher.redeemed_at} />
            ) : (
              <DateTime value={voucher.expires_at} />
            )
          }
        />
        {active ? (
          <Button
            variant="secondary"
            size="compact"
            width="fit"
            loading={loading}
            disabled={disabled}
            onClick={() => onShowQr(voucher)}
          >
            Show QR
          </Button>
        ) : (
          <RowFigure>
            <span className="type-body-sm">{fate(voucher)}</span>
          </RowFigure>
        )}
      </div>
    </RowItem>
  );
}
