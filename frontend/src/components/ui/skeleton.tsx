import { cn } from "@/lib/utils";

/**
 * Loading: a block in `--raised` with a slow opacity pulse (DESIGN.md §7). Give it the
 * shape of what it stands for. With reduced motion it is still.
 */
function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="skeleton"
      aria-hidden
      className={cn("rounded-md bg-raised motion-safe:animate-skeleton", className)}
      {...props}
    />
  );
}

export { Skeleton };
