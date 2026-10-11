import { RadioIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export type LiveBadgeProps = {
  /** Where the badge stands: a margin, an alignment. Its fill is not the caller's to set. */
  className?: string;
};

/**
 * Marks a live stream that is on (DESIGN.md §12): the word "Live" in the capitals of a
 * badge, the radio icon before it, on the white of the accent. A live stream has no colour
 * of its own (§4): the fill stands last among the classes, so no screen makes the badge
 * green or red. Nothing in it pulses, blinks or moves (§7). The icon is decoration in the
 * colour of the word, and a screen reader hears "Live".
 *
 * It is shown for what the API says: a live stream whose `status` is LIVE, or an item of
 * `GET /broadcasts/live`. It takes no time and reads no clock.
 */
export function LiveBadge({ className }: LiveBadgeProps) {
  return (
    <Badge className={cn(className, "bg-foreground text-primary-foreground")}>
      <RadioIcon aria-hidden />
      Live
    </Badge>
  );
}
