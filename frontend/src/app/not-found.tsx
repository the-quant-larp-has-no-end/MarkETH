import type { Metadata } from "next";

import { AppShell, NotFoundScreen } from "@/components/shell";

export const metadata: Metadata = { title: "Not found" };

/** Any path the app does not have (DESIGN.md §11): the "not found" screen, in the shell. */
export default function NotFound() {
  return (
    <AppShell>
      <NotFoundScreen />
    </AppShell>
  );
}
