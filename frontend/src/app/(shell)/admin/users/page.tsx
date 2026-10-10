import type { Metadata } from "next";
import { Suspense } from "react";

import { AdminUsersScreen } from "@/features/admin";

export const metadata: Metadata = { title: "Users" };

export default function Page() {
  return (
    <Suspense>
      <AdminUsersScreen />
    </Suspense>
  );
}
