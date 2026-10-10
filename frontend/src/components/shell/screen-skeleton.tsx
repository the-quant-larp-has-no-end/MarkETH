import { Skeleton } from "@/components/ui/skeleton";

/**
 * Skeleton blocks in the content column, for the moments before a screen can show its
 * own: start-up, and a route whose page is still on its way.
 */
export function ScreenSkeleton() {
  return (
    <div data-slot="screen-skeleton" role="status" className="flex flex-col gap-3">
      <span className="sr-only">Loading</span>
      <Skeleton className="h-8 w-2/3" />
      <Skeleton className="h-28 w-full rounded-lg" />
      <Skeleton className="h-28 w-full rounded-lg" />
      <Skeleton className="h-28 w-full rounded-lg" />
    </div>
  );
}
