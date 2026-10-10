"use client";

import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

/** The query parameter a code card's link carries: `/wallet?code=...` (DESIGN.md §11). */
const PARAMETER = "code";

/**
 * Takes `code` out of the address the browser shows, replacing the history entry, so that
 * no address, back step or bookmark keeps it.
 *
 * This is the browser's own `replaceState`, which the router follows: it happens at once
 * and asks the server for nothing. A navigation would leave the code in the address until
 * the route had answered.
 */
function removeFromAddress(): void {
  const address = new URL(window.location.href);
  if (!address.searchParams.has(PARAMETER)) return;
  address.searchParams.delete(PARAMETER);
  window.history.replaceState(null, "", `${address.pathname}${address.search}${address.hash}`);
}

/**
 * The code of a code card, when the Wallet was opened by its link (DESIGN.md §13).
 *
 * The code is read from the address once, when the Wallet opens, and removed from the
 * address at once. From then on it lives in memory, in the redeem sheet's field: nothing
 * is written to the browser's storage, and nothing is sent until the user taps "Redeem".
 * Null when the Wallet was opened without a code.
 */
export function useCodeFromAddress(): string | null {
  const address = useSearchParams();
  const [code] = useState(() => address.get(PARAMETER)?.trim() || null);

  useEffect(() => {
    removeFromAddress();
  }, []);

  return code;
}
