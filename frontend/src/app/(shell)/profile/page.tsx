import type { Metadata } from "next";
import { Suspense } from "react";

import { ProfileScreen } from "@/features/profile";

export const metadata: Metadata = { title: "Profile" };

export default function Page() {
  return (
    <Suspense>
      <ProfileScreen />
    </Suspense>
  );
}
