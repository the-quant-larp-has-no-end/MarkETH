"use client";

import { EmptyState } from "@/components/feedback";
import { Button } from "@/components/ui/button";

import { Link } from "./link";
import { Screen } from "./screen";

/**
 * The "not found" screen (DESIGN.md §13). It stands for a path the app does not have,
 * for a market the API answers `NOT_FOUND` for, and for the Admin and Barista routes
 * opened without their role: no permission lecture, the same screen.
 *
 * It is a whole screen with its own frame. A screen returns it in place of itself
 * (`if (market.error?.code === "NOT_FOUND") return <NotFoundScreen />`), never inside
 * its own `Screen`.
 */
export function NotFoundScreen() {
  return (
    <Screen top="main" title="Not found">
      <EmptyState
        illustration="polybahn"
        action={
          <Button variant="secondary" width="fit" asChild>
            <Link href="/">Browse markets</Link>
          </Button>
        }
      >
        This page does not exist. Not even in the HG basement.
      </EmptyState>
    </Screen>
  );
}
