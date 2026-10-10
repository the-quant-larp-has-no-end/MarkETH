import { PauseIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { MarketStatus, ResolutionOutcome } from "@/lib/api/schema";

/** `status` is a key, not copy: these are its words (DESIGN.md §12). */
const NAMES: Record<MarketStatus, string> = {
  PROPOSED: "In review",
  REJECTED: "Not approved",
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

export type StatusBadgeProps = {
  /** The `status` the API sends, which is already the effective one. */
  status: MarketStatus;
  /** The market's `outcome`: it decides the colour of a resolved market. */
  outcome?: ResolutionOutcome | null;
  className?: string;
};

/**
 * The status of a market as a badge (DESIGN.md §12). Green and red are for a resolved
 * outcome only; OPEN, CLOSED, HALTED and VOID are neutral.
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
      return (
        <Badge variant="muted" className={className}>
          {name}
        </Badge>
      );
  }
}
