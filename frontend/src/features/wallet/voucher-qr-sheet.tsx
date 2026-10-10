"use client";

import { ErrorBanner, InlineError } from "@/components/feedback";
import { ResponsiveSheet, SheetBody, SheetFooter } from "@/components/sheet";
import { Button } from "@/components/ui/button";
import { type ApiError, messageOf } from "@/lib/api/errors";
import type { VoucherCodeResponse, VoucherItem } from "@/lib/api/schema";

import { VoucherView } from "./voucher-view";
import type { Write } from "./write";

/** The voucher is used up, has expired, or is not the caller's: no code will come. */
function isGone(error: ApiError): boolean {
  return error.code === "CONFLICT" || error.code === "NOT_FOUND";
}

/**
 * "Show QR" for a voucher of the list (DESIGN.md §13). Every tap asks
 * `POST /vouchers/{voucher_id}/code` for a fresh code, and the code the voucher had
 * before stops working. The sheet opens with the tap and shows the voucher view:
 *
 * - while the code is on its way, with a skeleton where the QR tile will be;
 * - then with the answer's `code` and `expires_at`.
 * - `CONFLICT` and `NOT_FOUND` show the API's message instead; the list behind the sheet
 *   is read again and says what became of the voucher.
 * - Any other failure shows the banner of DESIGN.md §14 with "Try again": asking for a
 *   code once more is harmless.
 *
 * The code is in the answer the Wallet holds while this sheet is open, and nowhere else.
 * The sheet can be closed at any time; a code that arrives after that is shown to nobody.
 */
export function VoucherQrSheet({
  open,
  onClose,
  voucher,
  code,
}: {
  open: boolean;
  onClose: () => void;
  /** The voucher of the list the sheet is about. */
  voucher: VoucherItem | undefined;
  /** `POST /vouchers/{voucher_id}/code`, held by the Wallet. */
  code: Write<VoucherCodeResponse, string>;
}) {
  const failure = code.sending ? null : code.error;
  // An answer for another voucher is never shown under this one's name.
  const answer = code.data?.voucher_id === voucher?.id ? code.data : undefined;

  return (
    <ResponsiveSheet
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      title={voucher?.product_name ?? "Voucher"}
    >
      {failure === null ? (
        <VoucherView voucher={answer} onDone={onClose} />
      ) : (
        <>
          <SheetBody>
            {isGone(failure) ? (
              <InlineError>{messageOf(failure)}</InlineError>
            ) : (
              <ErrorBanner
                error={failure}
                onRetry={voucher === undefined ? undefined : () => void code.send(voucher.id)}
              />
            )}
          </SheetBody>
          <SheetFooter>
            <Button variant="secondary" width="full" onClick={onClose}>
              Close
            </Button>
          </SheetFooter>
        </>
      )}
    </ResponsiveSheet>
  );
}
