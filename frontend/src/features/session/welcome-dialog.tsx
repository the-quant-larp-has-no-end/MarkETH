"use client";

import { TokenIcon } from "@/components/brand";
import { BeanDrop } from "@/components/feedback";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import type { ProfileSavedResponse } from "@/lib/api/schema";
import { formatTokens } from "@/lib/format";

/**
 * The welcome dialog (DESIGN.md §13): opens once over the route that was opened, when
 * the answer that created the profile says `signup_granted`. The amount is `balance_cr`
 * of that same answer: a profile created a moment ago holds its welcome tokens and
 * nothing else. The bean drop plays once. It is a dialog at every width, and the one
 * piece of onboarding the product has.
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
  return (
    <Dialog
      open={open && saved !== null}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      {saved !== null && (
        <DialogContent showCloseButton={false} className="items-center gap-4 p-6 text-center">
          <TokenIcon size={64} />
          <BeanDrop arrival={saved.id}>
            <DialogTitle>You got {formatTokens(saved.balance_cr)}</DialogTitle>
          </BeanDrop>
          <DialogDescription className="type-body text-foreground">
            Spend them wisely, or on coffee. A share pays 1 token if you are right and nothing if
            you are wrong.
          </DialogDescription>
          <Button width="full" onClick={onClose}>
            Start trading
          </Button>
        </DialogContent>
      )}
    </Dialog>
  );
}
