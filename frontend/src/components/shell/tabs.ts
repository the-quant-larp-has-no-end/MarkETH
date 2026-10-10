import {
  BriefcaseBusinessIcon,
  type LucideIcon,
  ScanQrCodeIcon,
  ShieldCheckIcon,
  TrendingUpIcon,
  TrophyIcon,
  WalletIcon,
} from "lucide-react";

import type { UserRole } from "@/lib/api/schema";

export type Tab = {
  /** The name of the tab: what the top bar shows from 768 px. */
  label: string;
  /**
   * What the tab bar of a phone shows, where it is another word than `label`: a fifth of
   * a 320 px screen is 64 px wide, and no label there is ever cut.
   */
  short?: string;
  href: string;
  icon: LucideIcon;
  /** Whether a path belongs to this tab. */
  matches(pathname: string): boolean;
};

const under = (root: string) => (pathname: string) =>
  pathname === root || pathname.startsWith(`${root}/`);

const MARKETS: Tab = {
  label: "Markets",
  href: "/",
  icon: TrendingUpIcon,
  matches: (pathname) =>
    pathname === "/" || under("/markets")(pathname) || under("/propose")(pathname),
};
const PORTFOLIO: Tab = {
  label: "Portfolio",
  href: "/portfolio",
  icon: BriefcaseBusinessIcon,
  matches: under("/portfolio"),
};
const LEADERBOARD: Tab = {
  label: "Leaderboard",
  short: "Ranking",
  href: "/leaderboard",
  icon: TrophyIcon,
  matches: under("/leaderboard"),
};
const WALLET: Tab = {
  label: "Wallet",
  href: "/wallet",
  icon: WalletIcon,
  matches: under("/wallet"),
};
const SCAN: Tab = {
  label: "Scan",
  href: "/barista",
  icon: ScanQrCodeIcon,
  matches: under("/barista"),
};
const ADMIN: Tab = {
  label: "Admin",
  href: "/admin",
  icon: ShieldCheckIcon,
  matches: under("/admin"),
};

/**
 * The tabs a role sees (DESIGN.md §11). Everyone has Markets, Portfolio, Leaderboard and
 * Wallet; a barista also has Scan, an admin also has Admin and reaches the scanner from
 * there. There are never more than five.
 *
 * The Leaderboard tab reads "Ranking" in the tab bar of a phone and "Leaderboard" in the
 * top bar from 768 px, which is also the title of its screen.
 */
export function tabsFor(role: UserRole): readonly Tab[] {
  const everyone = [MARKETS, PORTFOLIO, LEADERBOARD, WALLET];
  if (role === "ADMIN") return [...everyone, ADMIN];
  if (role === "BARISTA") return [...everyone, SCAN];
  return everyone;
}
