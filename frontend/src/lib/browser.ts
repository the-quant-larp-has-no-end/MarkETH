/**
 * The one place that reloads the document. "Try again" on the full-screen message and
 * the recovery of the portal's session both go through it, and a test can watch it.
 */
export const page = {
  reload(): void {
    window.location.reload();
  },
};
