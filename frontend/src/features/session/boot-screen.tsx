import { BareTopBar, ScreenSkeleton } from "@/components/shell";

/**
 * The boot screen (DESIGN.md §13, Start-up): what stands in front of the app until it
 * knows who is looking. The top bar with the logo and nothing on its right, skeleton
 * blocks in the content column, no tab bar. It is also the HTML every page is served as.
 */
export function BootScreen() {
  return (
    <div data-slot="boot-screen" className="flex min-h-dvh flex-col">
      <BareTopBar />
      <main className="mx-auto flex w-full flex-1 flex-col px-4 pt-4 md:max-w-[45rem] lg:max-w-[67.5rem]">
        <ScreenSkeleton />
      </main>
    </div>
  );
}
