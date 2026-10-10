"use client";

import type { ReactNode } from "react";

import { NotFoundScreen } from "@/components/shell";
import { useMe } from "@/lib/api/session";

/**
 * The admin area (DESIGN.md §13): for role ADMIN, on every route under `/admin`. Anyone
 * else sees the "not found" screen, not a permission lecture. The check is for the look
 * of it only: the API decides what an admin may do, and the role is read from its
 * `GET /me` on every request.
 */
export function AdminLayout({ children }: { children: ReactNode }) {
  const { role } = useMe();
  if (role !== "ADMIN") return <NotFoundScreen />;
  return <>{children}</>;
}
