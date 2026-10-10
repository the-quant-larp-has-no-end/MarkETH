"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useSyncExternalStore } from "react";

import { ticketStore, type TicketState, type TicketStore } from "./ticket-store";

/**
 * The ticket of one market, and the store that changes it. The component renders what is
 * here and keeps none of it itself, so it shows the same thing after it was unmounted and
 * mounted again.
 */
export function useTicket(marketId: string): { ticket: TicketState; store: TicketStore } {
  const store = ticketStore(useQueryClient());
  const subscribe = useCallback(
    (onChange: () => void) => store.watch(marketId, onChange),
    [store, marketId],
  );
  const read = useCallback(() => store.read(marketId), [store, marketId]);

  return { ticket: useSyncExternalStore(subscribe, read, read), store };
}

const watchNothing = () => () => {};

/**
 * Whether a trade of this market is on its way: from Confirm, or from "Try again", until
 * its answer. It is for the surface that shows the ticket as a sheet: for that time the
 * sheet cannot be closed, so the verdict is seen where the trade was made (DESIGN.md §13).
 *
 * `shown` says that the sheet is open. Only then is the ticket followed: a closed sheet
 * shows no ticket, and a trade that is answered meanwhile must find none on screen, so
 * that its result is said with a toast.
 */
export function useTradeOut(marketId: string, shown: boolean): boolean {
  const store = ticketStore(useQueryClient());
  const subscribe = useCallback(
    (onChange: () => void) => (shown ? store.watch(marketId, onChange) : watchNothing()),
    [store, marketId, shown],
  );
  const read = useCallback(
    () => shown && store.read(marketId).attempt?.sending === true,
    [store, marketId, shown],
  );

  return useSyncExternalStore(subscribe, read, read);
}
