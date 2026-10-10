"use client";

import { ArrowLeftIcon, UserRoundIcon } from "lucide-react";
import { useRouter } from "next/navigation";

import { Logo } from "@/components/brand";
import { Amount } from "@/components/money";
import { useMe } from "@/lib/api/session";
import { DESKTOP_QUERY, WIDE_QUERY } from "@/lib/design";
import { formatTokens } from "@/lib/format";
import { useMediaQuery } from "@/lib/hooks";
import { cn } from "@/lib/utils";

import { Link } from "./link";
import { RouteProgress } from "./route-progress";
import { useShellStore } from "./shell-store";
import { TopTabs } from "./tab-bar";

const barClasses = "sticky top-0 z-40 bg-card pt-safe print:hidden";
const rowClasses = "relative mx-auto flex h-14 max-w-[67.5rem] items-center gap-2 px-4";

/**
 * The top bar before the app knows who is looking (DESIGN.md §13, Start-up): the logo
 * and nothing on its right.
 */
export function BareTopBar() {
  return (
    <header data-slot="top-bar" className={barClasses}>
      <div className={rowClasses}>
        <Logo />
      </div>
    </header>
  );
}

export type TopBarProps = {
  /**
   * `main`: the logo with name on the left, linking to Markets. `inner`: a back arrow
   * and the screen's title.
   */
  variant: "main" | "inner";
  /** The title of an inner screen. It is the screen's `h1`. */
  title?: string;
  /** Where the back arrow goes when the screen was opened directly. Markets by default. */
  backTo?: string;
};

/**
 * The top bar (DESIGN.md §11): 56 px tall, `--card`, sticky. On the right, always: the
 * balance as a pill linking to the Wallet, then the profile button. From 768 px the tabs
 * follow the logo on the same line; on an inner screen they do from 1024 px, where there
 * is room beside a title. The route progress line runs along its lower edge.
 */
export function TopBar({ variant, title, backTo = "/" }: TopBarProps) {
  const { balance_cr } = useMe();
  const router = useRouter();
  const store = useShellStore();
  const wide = useMediaQuery(WIDE_QUERY);
  const desktop = useMediaQuery(DESKTOP_QUERY);
  const showTabs = variant === "main" ? wide : desktop;

  return (
    <header data-slot="top-bar" className={barClasses}>
      <div className={rowClasses}>
        {variant === "main" ? (
          <Link href="/" className="-ml-2 inline-flex h-11 shrink-0 items-center rounded-md px-2">
            <Logo />
          </Link>
        ) : (
          <>
            <button
              type="button"
              aria-label="Back"
              onClick={() => {
                // One step back in the history; a screen that was opened directly has
                // none inside the app, and goes where its route says.
                if (store.canGoBack()) router.back();
                else router.push(backTo);
              }}
              className="-ml-2 inline-flex size-11 shrink-0 items-center justify-center rounded-md transition-colors duration-(--duration-fast) hover:bg-raised active:bg-raised"
            >
              <ArrowLeftIcon aria-hidden className="size-6" />
            </button>
            {title !== undefined && <h1 className="min-w-0 truncate type-h3">{title}</h1>}
          </>
        )}

        {showTabs && <TopTabs className={cn(variant === "main" ? "ml-2" : "ml-4")} />}

        <div className="ml-auto flex shrink-0 items-center gap-2">
          <Link
            href="/wallet"
            data-slot="balance"
            aria-label={`Wallet, ${formatTokens(balance_cr)}`}
            className="group inline-flex h-11 items-center rounded-full"
          >
            <span className="inline-flex h-9 items-center rounded-full bg-raised px-3 transition-opacity duration-(--duration-fast) group-hover:opacity-90 group-active:opacity-90">
              <Amount value={balance_cr} className="font-semibold" />
            </span>
          </Link>
          <Link
            href="/profile"
            aria-label="Profile"
            className="-mr-2 inline-flex size-11 items-center justify-center rounded-md transition-colors duration-(--duration-fast) hover:bg-raised active:bg-raised"
          >
            <UserRoundIcon aria-hidden className="size-6" />
          </Link>
        </div>
      </div>
      <RouteProgress />
    </header>
  );
}
