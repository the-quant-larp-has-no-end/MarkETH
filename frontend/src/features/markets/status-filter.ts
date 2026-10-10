import type { Chip } from "@/components/controls";
import type { MarketStatus } from "@/lib/api/schema";

/** The four chips of the Markets list (DESIGN.md §13). */
export type StatusFilter = "open" | "closed" | "resolved" | "all";

/** The list opens on "Open". */
export const DEFAULT_FILTER: StatusFilter = "open";

export const STATUS_CHIPS: readonly Chip<StatusFilter>[] = [
  { value: "open", label: "Open" },
  { value: "closed", label: "Closed" },
  { value: "resolved", label: "Resolved" },
  { value: "all", label: "All" },
];

/**
 * What each chip sends as `status` to `GET /markets`, which takes the parameter several
 * times. "Open" asks for HALTED too, so that a paused market stays in sight; "Resolved"
 * asks for VOID too. "All" sends none: the API then lists every status but those of a
 * proposal (PROPOSED, REJECTED and EXPIRED) by itself.
 */
const STATUSES: Record<StatusFilter, readonly MarketStatus[] | undefined> = {
  open: ["OPEN", "HALTED"],
  closed: ["CLOSED"],
  resolved: ["RESOLVED", "VOID"],
  all: undefined,
};

export function statusesOf(filter: StatusFilter): readonly MarketStatus[] | undefined {
  return STATUSES[filter];
}
