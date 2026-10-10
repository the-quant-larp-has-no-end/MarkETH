import { toast } from "sonner";
import { vi } from "vitest";

/**
 * An animation frame of the page as jsdom runs it, kept from before a test can put a
 * stub or a clock of its own in its place.
 */
const nextFrame = window.requestAnimationFrame.bind(window);

/**
 * Puts every toast away, and waits until nothing about a toast is on its way any more.
 *
 * sonner keeps its toasts outside React, in one store for the page, and a Toaster that
 * mounts is handed whatever that store still holds. Two things of one test can so reach
 * the Toaster of the next:
 *
 * - a toast that is still up, which is why every toast is put away here;
 * - the news that a toast was put away. sonner tells its Toasters of that one animation
 *   frame after the fact. By then the Toaster that listens is the next test's, and it
 *   takes the news for a toast of its own with the same id, as two tests that tell the
 *   same notice have: the toast leaves the page behind sonner's back, the store goes on
 *   holding it, and the Toaster that mounts after that one shows it again.
 *
 * So the frame passes here, where the test's Toaster is gone and nobody listens. A store
 * that never held a toast has no news on its way, and is not waited for.
 *
 * The set-up calls this after every test. A test that stands for two page loads in a row
 * calls it between them: a page that loads has a store of its own.
 */
export async function clearToasts(): Promise<void> {
  toast.dismiss();
  if (toast.getHistory().length === 0) return;
  // A clock a test put in place of the page's would hold the frame back for good.
  vi.useRealTimers();
  await new Promise<void>((resolve) => {
    nextFrame(() => resolve());
  });
}
