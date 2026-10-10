"use client";

import { Banner } from "@/components/feedback";
import { Screen } from "@/components/shell";
import { Button } from "@/components/ui/button";
import { GENERIC_MESSAGE } from "@/lib/api/errors";

/**
 * A screen that failed to render. This is a fault of the app, not an answer of the API,
 * so there is no message of the API to show: the banner says the one sentence DESIGN.md
 * §14 has for that, and "Try again" renders the route afresh. The shell stays.
 */
export default function RouteError({
  retry,
  reset,
}: {
  error: Error & { digest?: string };
  retry?: () => void;
  reset?: () => void;
}) {
  const again = retry ?? reset;

  return (
    <Screen top="main" title="Something went wrong">
      <Banner
        tone="error"
        action={
          again !== undefined && (
            <Button variant="secondary" size="compact" width="fit" onClick={() => again()}>
              Try again
            </Button>
          )
        }
      >
        {GENERIC_MESSAGE}
      </Banner>
    </Screen>
  );
}
