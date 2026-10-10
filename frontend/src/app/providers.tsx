"use client";

import { QueryClientProvider } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";

import { Toaster } from "@/components/ui/sonner";
import { AppGate } from "@/features/session";
import { createQueryClient } from "@/lib/api/query-client";

/**
 * What every route stands in: the query client, the start-up gate and the toasts. The
 * gate shows the boot screen until `/config` and `/me` have answered, so the HTML of
 * every page is that boot screen and all data arrives in the browser.
 */
export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(createQueryClient);

  return (
    <QueryClientProvider client={client}>
      <AppGate>{children}</AppGate>
      <Toaster />
    </QueryClientProvider>
  );
}
