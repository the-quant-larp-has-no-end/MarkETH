"use client";

import { type ComponentType, type RefObject, useRef, useState } from "react";

import { Banner } from "@/components/feedback";
import { Button } from "@/components/ui/button";
import type { MarketDetailResponse, MarketStatus } from "@/lib/api/schema";

import { ApproveDialog } from "./approve-dialog";
import {
  CloseDialog,
  HaltDialog,
  type MarketDialogProps,
  RejectDialog,
  ResolveDialog,
  ResumeDialog,
  VoidDialog,
} from "./market-dialogs";

type Action = "approve" | "reject" | "halt" | "resume" | "close" | "resolve" | "void";

/**
 * The actions a market's status allows (SPEC §6, DESIGN.md §13), in the order the bar
 * shows them. A market that is rejected, resolved or voided has reached its end.
 */
const ALLOWED: Record<MarketStatus, readonly Action[]> = {
  PROPOSED: ["approve", "reject"],
  OPEN: ["halt", "close"],
  HALTED: ["resume", "close"],
  CLOSED: ["resolve", "void"],
  REJECTED: [],
  RESOLVED: [],
  VOID: [],
};

const ACTIONS: Record<
  Action,
  {
    label: string;
    /** The action that moves the market on is the one white button of the screen. */
    primary?: boolean;
    /** Resolve and void follow `viewer.can_settle`. */
    settles?: boolean;
    dialog: ComponentType<MarketDialogProps>;
  }
> = {
  approve: { label: "Approve", primary: true, dialog: ApproveDialog },
  reject: { label: "Reject", dialog: RejectDialog },
  halt: { label: "Halt", dialog: HaltDialog },
  resume: { label: "Resume", primary: true, dialog: ResumeDialog },
  close: { label: "Close", dialog: CloseDialog },
  resolve: { label: "Resolve", primary: true, settles: true, dialog: ResolveDialog },
  void: { label: "Void", settles: true, dialog: VoidDialog },
};

/** The sentence the API answers both settling actions with for an admin who traded. */
const TRADED_MESSAGE = "You traded this market, so another admin has to settle it.";

/**
 * The action bar of the admin view of a market (DESIGN.md §13): only the actions the
 * market's status allows. Each opens a dialog that asks for what the action needs; the
 * four that cannot be taken back state their consequence in an alert-dialog.
 *
 * Resolve and Void follow `viewer.can_settle`: for an admin who traded the market both
 * are disabled, and a banner above the bar says why in the API's own sentence.
 *
 * A dialog that is open stays open when the market changes behind it, so that the API's
 * message for a refused action can be read: a `CONFLICT` means the market is no longer
 * what the dialog thought, and the bar behind it already shows what it is now.
 */
export function MarketActions({
  market,
  returnFocusTo,
}: {
  market: MarketDetailResponse;
  /**
   * Where the focus goes after an action took its own button off the screen: the
   * question, from where a screen reader reads the status the market has now.
   */
  returnFocusTo: RefObject<HTMLElement | null>;
}) {
  // `round` gives every opening a dialog of its own, with empty fields and no old answer.
  const [dialog, setDialog] = useState<{ action: Action; round: number } | null>(null);
  const [open, setOpen] = useState(false);
  const opener = useRef<HTMLButtonElement | null>(null);

  const allowed = ALLOWED[market.status];
  const cannotSettle = !market.viewer.can_settle;
  const Dialog = dialog === null ? null : ACTIONS[dialog.action].dialog;

  const blocked = cannotSettle && allowed.some((action) => ACTIONS[action].settles === true);

  return (
    <>
      {allowed.length > 0 && (
        <section aria-label="Actions" data-slot="market-actions" className="flex flex-col gap-3">
          {blocked && <Banner>{TRADED_MESSAGE}</Banner>}
          <div className="grid grid-cols-2 gap-2 md:flex">
            {allowed.map((action) => {
              const { label, primary, settles } = ACTIONS[action];
              return (
                <Button
                  key={action}
                  variant={primary === true ? "primary" : "secondary"}
                  disabled={settles === true && cannotSettle}
                  onClick={(event) => {
                    opener.current = event.currentTarget;
                    setDialog((last) => ({ action, round: (last?.round ?? 0) + 1 }));
                    setOpen(true);
                  }}
                >
                  {label}
                </Button>
              );
            })}
          </div>
        </section>
      )}
      {dialog !== null && Dialog !== null && (
        <Dialog
          key={dialog.round}
          market={market}
          open={open}
          onClose={() => setOpen(false)}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            const from = opener.current;
            const back = from !== null && from.isConnected && !from.disabled;
            (back ? from : returnFocusTo.current)?.focus();
          }}
        />
      )}
    </>
  );
}
