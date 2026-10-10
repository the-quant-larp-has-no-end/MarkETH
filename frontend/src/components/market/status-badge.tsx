import { PauseIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { MarketStatus, ResolutionOutcome } from "@/lib/api/schema";

export type StatusBadgeProps = {
  /** The `status` the API sends, which is already the effective one. */
  status: MarketStatus;
  /** The market's `outcome`: it decides the colour of a resolved market. */
  outcome?: ResolutionOutcome | null;
  className?: string;
};

/**
 * The status of a market as a badge (DESIGN.md §12). `status` is a key, not copy: the
 * words are here. Green and red are for a resolved outcome only; OPEN, CLOSED, HALTED
 * and VOID are neutral.
 *
 * A market card shows no badge while the market is open for trading; that is the card's
 * decision, from `trading_open`.
 */
export function StatusBadge({ status, outcome, className }: StatusBadgeProps) {
  switch (status) {
    case "OPEN":
      return (
        <Badge variant="raised" className={className}>
          Open
        </Badge>
      );
    case "CLOSED":
      return (
        <Badge variant="muted" className={className}>
          Closed
        </Badge>
      );
    case "HALTED":
      return (
        <Badge variant="outline" className={className}>
          <PauseIcon aria-hidden />
          Paused
        </Badge>
      );
    case "RESOLVED":
      if (outcome === "YES") {
        return (
          <Badge variant="yes" className={className}>
            Resolved YES
          </Badge>
        );
      }
      if (outcome === "NO") {
        return (
          <Badge variant="no" className={className}>
            Resolved NO
          </Badge>
        );
      }
      return (
        <Badge variant="muted" className={className}>
          Resolved
        </Badge>
      );
    case "VOID":
      return (
        <Badge variant="muted" className={className}>
          Voided
        </Badge>
      );
    case "PROPOSED":
      return (
        <Badge variant="raised" className={className}>
          In review
        </Badge>
      );
    case "REJECTED":
      return (
        <Badge variant="muted" className={className}>
          Not approved
        </Badge>
      );
  }
}
