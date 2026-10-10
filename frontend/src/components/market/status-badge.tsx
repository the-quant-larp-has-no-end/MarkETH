import { PauseIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { MarketStatus, ResolutionOutcome } from "@/lib/api/schema";

/** `status` is a key, not copy: these are its words (DESIGN.md §12). */
const NAMES: Record<MarketStatus, string> = {
  PROPOSED: "In review",
  REJECTED: "Not approved",
  EXPIRED: "Expired",
  OPEN: "Open",
  HALTED: "Paused",
  CLOSED: "Closed",
  RESOLVED: "Resolved",
  VOID: "Voided",
};

/** The word for a status, as its badge says it: for a choice of statuses, a table heading. */
export function statusName(status: MarketStatus): string {
  return NAMES[status];
}

/**
 * A market that was never opened for trading: a proposal that waits for its review, one
 * VIS did not approve, and one whose close time passed before anybody reviewed it. The API
 * shows these to their proposer and to admins only. Such a market has no price, no chart
 * and no positions.
 */
export function isProposal(status: MarketStatus): boolean {
  return status === "PROPOSED" || status === "REJECTED" || status === "EXPIRED";
}

export type StatusBadgeProps = {
  /** The `status` the API sends, which is already the effective one. */
  status: MarketStatus;
  /** The market's `outcome`: it decides the colour of a resolved market. */
  outcome?: ResolutionOutcome | null;
  className?: string;
};

/**
 * The status of a market as a badge (DESIGN.md §12). Green and red are for a resolved
 * outcome only; every other status is neutral. A state that is over and decided nothing
 * (CLOSED, VOID, REJECTED, EXPIRED) is the quiet one: `--raised` with muted text.
 *
 * A market card shows no badge while the market is open for trading; that is the card's
 * decision, from `trading_open`.
 */
export function StatusBadge({ status, outcome, className }: StatusBadgeProps) {
  const name = statusName(status);

  switch (status) {
    case "OPEN":
    case "PROPOSED":
      return (
        <Badge variant="raised" className={className}>
          {name}
        </Badge>
      );
    case "HALTED":
      return (
        <Badge variant="outline" className={className}>
          <PauseIcon aria-hidden />
          {name}
        </Badge>
      );
    case "RESOLVED":
      if (outcome === "YES" || outcome === "NO") {
        return (
          <Badge variant={outcome === "YES" ? "yes" : "no"} className={className}>
            {name} {outcome}
          </Badge>
        );
      }
      return (
        <Badge variant="muted" className={className}>
          {name}
        </Badge>
      );
    case "CLOSED":
    case "VOID":
    case "REJECTED":
    case "EXPIRED":
      return (
        <Badge variant="muted" className={className}>
          {name}
        </Badge>
      );
  }
}
