import type { ReactNode } from "react";

import { Illustration, type IllustrationName } from "@/components/brand";
import { cn } from "@/lib/utils";

/**
 * An empty state (DESIGN.md §12): one pixel illustration, one sentence, and at most one
 * button. The campus lines of DESIGN.md §3 live here, one per screen at most.
 */
export function EmptyState({
  illustration,
  children,
  action,
  className,
}: {
  /** One of the illustration slots; leave it out for a plain sentence. */
  illustration?: IllustrationName;
  /** The sentence. */
  children: ReactNode;
  /** At most one button or link. */
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      data-slot="empty-state"
      className={cn("flex flex-col items-center gap-4 px-4 py-12 text-center", className)}
    >
      {illustration !== undefined && <Illustration name={illustration} />}
      <p className="max-w-80 type-body text-muted-foreground">{children}</p>
      {action}
    </div>
  );
}
