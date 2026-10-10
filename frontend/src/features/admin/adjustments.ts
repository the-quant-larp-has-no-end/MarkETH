import { toast } from "sonner";

import type { ApiError } from "@/lib/api/errors";
import { adjustUserBalanceMutation } from "@/lib/api/mutations";
import type { AdjustBalanceRequest, BalanceAdjustmentResponse } from "@/lib/api/schema";
import type { SubmissionRules } from "@/lib/api/submissions";

/** Whether an adjustment adds tokens to a balance or takes them from it. */
export type Direction = "add" | "remove";

/**
 * What the dialog showed when an adjustment went out: enough to show the same adjustment
 * for as long as it waits for its verdict, also in a dialog that was closed and opened
 * again.
 */
export type SentAdjustment = {
  direction: Direction;
  /** The amount as it was typed, without a sign. */
  amount: string;
  reason: string;
};

/** What the dialog of one user holds beside the adjustment that waits: the API's refusal. */
export type AdjustmentRest = { refusal: ApiError | null };

/** What a toast says once an adjustment is booked. */
export const ADJUSTED = "Tokens adjusted";

/** A dialog with nothing refused. The store finds an untouched user by this very object. */
export const NOTHING_REFUSED: AdjustmentRest = { refusal: null };

/**
 * The adjustment of a user's tokens as a kept write (DESIGN.md §14, "An adjustment of
 * tokens"): one body and one Idempotency-Key from the confirming tap, kept until a 2xx or
 * a 4xx other than 429 and sent again unchanged by "Try again". The store of
 * `lib/api/submissions.ts` does the keeping, per user; this says what it holds beside.
 *
 * A refusal is kept for the dialog to show. An adjustment that is booked while no dialog
 * shows its user is said with a toast: a dialog that is there says it itself and closes.
 */
export const ADJUSTMENTS: SubmissionRules<
  AdjustBalanceRequest,
  BalanceAdjustmentResponse,
  SentAdjustment,
  AdjustmentRest
> = {
  mutation: adjustUserBalanceMutation,
  untouched: NOTHING_REFUSED,
  answered: ({ onScreen }) => {
    if (!onScreen) toast(ADJUSTED);
    return NOTHING_REFUSED;
  },
  refused: ({ error }) => ({ refusal: error }),
};
