import { StatusBadge, type StatusBadgeProps, statusName } from "@/components/market";
import { Badge } from "@/components/ui/badge";
import type { MarketStatus } from "@/lib/api/schema";

/** The word for a proposal whose close time passed before an admin decided on it. */
const EXPIRED = "Expired";

/** The word for a status, as its badge says it: for the status select of the admin area. */
export function marketStatusName(status: MarketStatus): string {
  return status === "EXPIRED" ? EXPIRED : statusName(status);
}

/**
 * The status of a market as the admin area shows it: the badge of DESIGN.md §12, and
 * "Expired" for a proposal the API reports as EXPIRED. Such a proposal has reached its
 * end like one that was not approved, and its badge looks the same: `--raised` with
 * `--muted-foreground` text.
 */
export function MarketStatusBadge({ status, outcome, className }: StatusBadgeProps) {
  if (status === "EXPIRED") {
    return (
      <Badge variant="muted" className={className}>
        {EXPIRED}
      </Badge>
    );
  }
  return <StatusBadge status={status} outcome={outcome} className={className} />;
}
