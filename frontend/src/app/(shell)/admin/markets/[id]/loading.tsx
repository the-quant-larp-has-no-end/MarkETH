import { Screen, ScreenSkeleton } from "@/components/shell";

/** Shown at once while the page of one market is on its way from the server. */
export default function Loading() {
  return (
    <Screen top="inner" backTo="/admin">
      <ScreenSkeleton />
    </Screen>
  );
}
