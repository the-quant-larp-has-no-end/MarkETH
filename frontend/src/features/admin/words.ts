import type { MarketStatus, UserRole, UserStatus } from "@/lib/api/schema";

/**
 * The statuses a market is reported with, in the order it passes through them: the seven
 * its row keeps, and EXPIRED, which the API reports for a proposal whose close time passed
 * before an admin decided on it. "All" in the admin area asks for every one of them by
 * name, because a request without `status` leaves PROPOSED, EXPIRED and REJECTED out
 * (DESIGN.md §13).
 */
export const ALL_STATUSES = [
  "PROPOSED",
  "EXPIRED",
  "REJECTED",
  "OPEN",
  "HALTED",
  "CLOSED",
  "RESOLVED",
  "VOID",
] as const satisfies readonly MarketStatus[];

/** `role` is a key, not copy: these are its words (DESIGN.md §13, Users). */
const ROLE_NAMES: Record<UserRole, string> = {
  USER: "User",
  BARISTA: "Barista",
  ADMIN: "Admin",
};

/** The roles in the order a select offers them. */
export const ROLES = ["USER", "BARISTA", "ADMIN"] as const satisfies readonly UserRole[];

export function roleName(role: UserRole): string {
  return ROLE_NAMES[role];
}

/** What a role lets its holder do, in one line under the role select. */
const ROLE_LINES: Record<UserRole, string> = {
  USER: "Trades and proposes markets.",
  BARISTA: "Also scans vouchers at the bar.",
  ADMIN: "Also runs the admin area: markets, codes, risk and users.",
};

export function roleLine(role: UserRole): string {
  return ROLE_LINES[role];
}

/** `status` of a user is a key too. */
const USER_STATUS_NAMES: Record<UserStatus, string> = {
  ACTIVE: "Active",
  SUSPENDED: "Suspended",
};

export const USER_STATUSES = ["ACTIVE", "SUSPENDED"] as const satisfies readonly UserStatus[];

export function userStatusName(status: UserStatus): string {
  return USER_STATUS_NAMES[status];
}
