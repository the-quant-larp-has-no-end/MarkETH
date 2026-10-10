import { Screen, ScreenSkeleton } from "@/components/shell";

/**
 * A market's page is made per request, so its route would otherwise wait for the server
 * before anything changes on screen. This is shown at once in its place.
 */
export default function Loading() {
  return (
    <Screen top="inner">
      <ScreenSkeleton />
    </Screen>
  );
}
