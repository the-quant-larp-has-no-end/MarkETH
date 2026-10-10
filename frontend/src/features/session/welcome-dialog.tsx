"use client";

import { TokenIcon } from "@/components/brand";
import { BeanDrop } from "@/components/feedback";
import { ResponsiveSheet, SheetBody, SheetFooter } from "@/components/sheet";
import { Button } from "@/components/ui/button";
import type { ProfileSavedResponse } from "@/lib/api/schema";
import { formatTokens } from "@/lib/format";

/**
 * The welcome dialog (DESIGN.md §13): opens once over the route that was opened, when
 * the answer that created the profile says `signup_granted`. The amount is `balance_cr`
 * of that same answer: a profile created a moment ago holds its welcome tokens and
 * nothing else. The bean drop plays once. It is a dialog at every width, and the one
 * piece of onboarding the product has.
 *
 * "Start trading" and the close cross both close it, and so do Escape and a tap beside
 * it.
 */
export function WelcomeDialog({
  saved,
  open,
  onClose,
}: {
  /** The answer of the save that created the profile. */
  saved: ProfileSavedResponse | null;
  open: boolean;
  onClose: () => void;
}) {
  const headline = saved === null ? "" : `You got ${formatTokens(saved.balance_cr)}`;

  return (
    <ResponsiveSheet
      dialog
      open={open && saved !== null}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      // The dialog is named by its headline, which the eye finds under the token icon.
      title={headline}
      hideTitle
    >
      {saved !== null && (
        <>
          <SheetBody className="items-center px-6 text-center">
            <TokenIcon size={64} />
            <BeanDrop arrival={saved.id}>
              <p aria-hidden className="type-h2">
                {headline}
              </p>
            </BeanDrop>
            <p className="type-body">
              Spend them wisely, or on coffee. A share pays 1 token if you are right and nothing if
              you are wrong.
            </p>
          </SheetBody>
          <SheetFooter>
            <Button width="full" onClick={onClose}>
              Start trading
            </Button>
          </SheetFooter>
        </>
      )}
    </ResponsiveSheet>
  );
}
