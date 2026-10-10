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
