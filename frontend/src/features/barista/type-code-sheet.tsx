"use client";

import type { FormEvent } from "react";

import { CodeInput } from "@/components/form";
import { ResponsiveSheet, SheetBody, SheetFooter } from "@/components/sheet";
import { Button } from "@/components/ui/button";
import { useConfig } from "@/lib/api/session";
import { codeCharacters } from "@/lib/format";

/**
 * The typed alternative to the camera (DESIGN.md §12, §15): the characters under a
 * voucher's QR, typed in. The field shows them in groups of four as they are typed and
 * takes as many as a voucher code has (`voucher_code_length` of `/config`).
 *
 * The sheet only collects the code. What becomes of it is the scanner's to show: a typed
 * code ends in the same full-screen result as a scanned one. Like every sheet it has the
 * close cross at its top right, which puts it away and sends nothing.
 */
export function TypeCodeSheet({
  open,
  onOpenChange,
  code,
  onCodeChange,
  onSubmit,
  disabled,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** What the field holds. The scanner keeps it, so a rejected code can be corrected. */
  code: string;
  onCodeChange: (code: string) => void;
  /** Called with a whole code in the field. */
  onSubmit: () => void;
  /** Nothing can be sent now: a voucher is being checked, or the API asked to wait. */
  disabled: boolean;
}) {
  const { voucher_code_length } = useConfig();
  const complete = codeCharacters(code).length === voucher_code_length;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (complete && !disabled) onSubmit();
  };

  return (
    <ResponsiveSheet open={open} onOpenChange={onOpenChange} title="Type the voucher code">
      <form noValidate onSubmit={submit} className="flex min-h-0 flex-1 flex-col">
        <SheetBody>
          <CodeInput
            label="Voucher code"
            name="voucher-code"
            value={code}
            onChange={onCodeChange}
            length={voucher_code_length}
            enterKeyHint="done"
          />
        </SheetBody>
        <SheetFooter>
          <Button type="submit" width="full" disabled={!complete || disabled}>
            Redeem voucher
          </Button>
        </SheetFooter>
      </form>
    </ResponsiveSheet>
  );
}
