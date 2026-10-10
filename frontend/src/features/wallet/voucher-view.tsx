"use client";

import { SunIcon } from "lucide-react";

import { Illustration } from "@/components/brand";
import { QrCode } from "@/components/qr";
import { SheetBody, SheetFooter } from "@/components/sheet";
import { DateTime } from "@/components/time";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

/** What the API says of a voucher in the one answer that carries its code. */
export type ShownVoucher = { code: string; expires_at: string };

/**
 * A voucher, ready to be shown at the bar (DESIGN.md §13): the QR tile that holds its
 * code, the code as text, "Valid until", the line for the bar, the brightness hint and
 * "Done". The sheet around it is titled with the product. The view after getting a
 * voucher and the one behind "Show QR" are this same view.
 *
 * The code comes in as a prop and is kept nowhere. Without a voucher the tile is a
 * skeleton of itself: a fresh code is on its way, or the view is closing and has already
 * forgotten the one it showed.
 */
export function VoucherView({
  voucher,
  onDone,
}: {
  voucher: ShownVoucher | undefined;
  onDone: () => void;
}) {
  return (
    <>
      <SheetBody className="items-center text-center">
        {voucher === undefined ? (
          <div
            data-slot="voucher-skeleton"
            role="status"
            className="flex w-full flex-col items-center gap-3"
          >
            <span className="sr-only">Loading</span>
            <Skeleton className="aspect-square w-full max-w-72 min-w-60 rounded-lg" />
            <Skeleton className="h-7 w-40" />
            <Skeleton className="h-5 w-48" />
          </div>
        ) : (
          <>
            <QrCode
              value={voucher.code}
              label="Voucher QR code"
              code={voucher.code}
              className="w-full"
            />
            <p className="type-body-sm text-muted-foreground">
              Valid until <DateTime value={voucher.expires_at} />
            </p>
          </>
        )}
        <div className="flex flex-col items-center gap-2">
          <Illustration name="steaming-cup" />
          <p className="type-body">Show this at the bar. You earned it, sort of.</p>
        </div>
        <p className="flex items-center gap-2 type-body-sm text-muted-foreground">
          <SunIcon aria-hidden className="size-5 shrink-0" />
          Turn up your brightness
        </p>
      </SheetBody>
      <SheetFooter>
        <Button width="full" onClick={onDone}>
          Done
        </Button>
      </SheetFooter>
    </>
  );
}
