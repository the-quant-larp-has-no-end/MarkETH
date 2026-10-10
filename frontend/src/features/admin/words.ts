import type { MarketStatus } from "@/lib/api/schema";

/**
 * The seven statuses of a market, in the order it passes through them. "All" in the
 * admin area asks for every one of them by name, because a request without `status`
 * leaves PROPOSED and REJECTED out (DESIGN.md §13).
 */
export const ALL_STATUSES = [
  "PROPOSED",
  "REJECTED",
  "OPEN",
  "HALTED",
  "CLOSED",
  "RESOLVED",
  "VOID",
] as const satisfies readonly MarketStatus[];
