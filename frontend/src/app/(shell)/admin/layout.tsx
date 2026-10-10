import type { ReactNode } from "react";

import { AdminLayout } from "@/features/admin";

/** The admin area: for role ADMIN, with its three tabs (DESIGN.md §13). */
export default function Layout({ children }: { children: ReactNode }) {
  return <AdminLayout>{children}</AdminLayout>;
}
