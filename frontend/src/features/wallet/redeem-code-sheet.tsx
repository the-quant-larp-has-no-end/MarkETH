"use client";

import { ScanQrCodeIcon } from "lucide-react";
import { type FormEvent, useState } from "react";

import { ErrorBanner, RetryCountdown } from "@/components/feedback";
import { CodeInput } from "@/components/form";
import { QrScanner } from "@/components/qr";
import { ResponsiveSheet, SheetBody, SheetFooter } from "@/components/sheet";
import { Button } from "@/components/ui/button";
import { messageOf } from "@/lib/api/errors";
import type { RedeemCodeResponse } from "@/lib/api/schema";
import { useConfig } from "@/lib/api/session";
import { codeCharacters, formatCode, readScannedCode } from "@/lib/format";
import { useRetryAfter } from "@/lib/hooks";

import type { Write } from "./write";

/** What the sheet says when a scanned QR holds a link that carries no code. */
const NO_CODE = "No code in this QR.";

/**
 * "Redeem code" (DESIGN.md §13): the code field, "Scan QR" and "Redeem", which sends the
 * code alone to `POST /codes/redeem`.
 *
 * - The field shows the code in groups of four as it is typed, and takes the code of a
 *   code card: from the address the Wallet was opened with (`initialCode`), or from the
 *   scanner, which takes the `code` out of a card's link. A link is never put into the
 *   field, so it is never sent.
 * - Nothing is sent by a filled field alone: the user taps "Redeem".
 * - A refusal shows the API's `message` under the field and nothing of ours. For a code
 *   that cannot be redeemed that is always the same sentence, and the sheet never hints
 *   at why. After `RATE_LIMITED` the button waits out the time the API names.
 * - A request that got no answer shows the banner of DESIGN.md §14. "Redeem" works again
 *   once the balance and the Activity have been read again: they say whether the code
 *   counted.
 *
 * The field and what the API said about it stay when the sheet is closed and opened
 * again; a redeemed code empties the field.
 */
export function RedeemCodeSheet({
  open,
  onClose,
  initialCode,
  redeem,
  onRedeemed,
}: {
  open: boolean;
  onClose: () => void;
  /** The code of the code card the Wallet was opened with, or null. */
  initialCode: string | null;
  /** `POST /codes/redeem`, held by the Wallet so that it outlives the sheet. */
  redeem: Write<RedeemCodeResponse, string>;
  /** Called with the API's answer once the code is redeemed. */
  onRedeemed: (redeemed: RedeemCodeResponse) => void;
}) {
  const { code_length } = useConfig();
  const [code, setCode] = useState(() => formatCode(initialCode ?? "", code_length));
  const [notice, setNotice] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);
  const wait = useRetryAfter(redeem.error);

  const failure = redeem.unanswered ?? redeem.error;
  const refusal = failure !== null && !failure.outcomeUnknown ? failure : null;
  const noAnswer = failure !== null && failure.outcomeUnknown ? failure : null;
  const complete = codeCharacters(code).length === code_length;

  /**
   * An answer is about the code that was sent: it holds until the field is typed in or
   * scanned into. The wait after `RATE_LIMITED` is the exception: it
   * belongs to the caller, whatever the code.
   */
  const dropAnswer = () => {
    if (wait > 0) return;
    redeem.forget();
    redeem.dismiss();
  };

  const replaceCode = (next: string) => {
    setCode(next);
    setNotice(null);
    dropAnswer();
  };

  const scanned = (text: string) => {
    setScanning(false);
    const read = readScannedCode(text);
    const next = read === null ? "" : formatCode(read, code_length);
    if (next !== "") return replaceCode(next);
    // A link without a code, or nothing a code could be made of: the field stays.
    dropAnswer();
    setNotice(NO_CODE);
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!complete || wait > 0) return;
    setNotice(null);
    redeem.send(code, {
      onSuccess: (redeemed) => {
        setCode("");
        onRedeemed(redeemed);
      },
    });
  };

  return (
    <ResponsiveSheet
      open={open}
      onOpenChange={(next) => {
        if (next) return;
        setScanning(false);
        onClose();
      }}
      title="Redeem code"
      dismissible={!redeem.sending}
    >
      {scanning ? (
        <SheetBody>
          <QrScanner
            onScan={scanned}
            alternativeLabel="Type the code instead"
            onAlternative={() => setScanning(false)}
          />
        </SheetBody>
      ) : (
        <form noValidate onSubmit={submit} className="flex min-h-0 flex-1 flex-col">
          <SheetBody>
            <CodeInput
              label="Code"
              name="code"
              value={code}
              onChange={replaceCode}
              length={code_length}
              disabled={redeem.sending}
              enterKeyHint="done"
              error={refusal === null ? notice : messageOf(refusal)}
            />
            <RetryCountdown error={refusal} />
            <Button
              variant="secondary"
              width="full"
              disabled={redeem.sending}
              onClick={() => setScanning(true)}
            >
              <ScanQrCodeIcon aria-hidden />
              Scan QR
            </Button>
          </SheetBody>
          <SheetFooter>
            {noAnswer !== null && (
              <ErrorBanner
                error={redeem.rereadError ?? noAnswer}
                onRetry={redeem.rereadError === null ? undefined : redeem.reread}
              />
            )}
            <Button
              type="submit"
              width="full"
              loading={redeem.sending}
              disabled={!complete || redeem.blocked || wait > 0}
            >
              Redeem
            </Button>
          </SheetFooter>
        </form>
      )}
    </ResponsiveSheet>
  );
}
