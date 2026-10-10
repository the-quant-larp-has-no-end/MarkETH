import type { ReactNode } from "react";

import { AppShell } from "@/components/shell";

/** Every route of the app stands in the app shell (DESIGN.md §11). */
export default function ShellLayout({ children }: { children: ReactNode }) {
  return <AppShell>{children}</AppShell>;
}
