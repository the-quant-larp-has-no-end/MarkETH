import { page } from "./browser";

/**
 * The portal's 401 means reload, once (DESIGN.md §14).
 *
 * The portal signs a visitor in when a document is requested, never on an API call. So
 * when an API call comes back with the portal's 401, the page reloads: the portal signs
 * the visitor in and returns to the same address. A mark that survives the reload says
 * that this has been tried; if the same answer comes again the page stays, and start-up
 * shows "Your ETH sign-in has ended." A `/me` that answers removes the mark.
 *
 * The mark is one flag in the tab's session storage. Nothing else of the app is stored
 * in the browser.
 */
const MARK = "marketh.reloaded";

function read(): boolean | null {
  try {
    return window.sessionStorage.getItem(MARK) !== null;
  } catch {
    return null;
  }
}

/**
 * Reloads the page unless that has been tried already. Returns whether it is reloading.
 * Without session storage it does not reload: a reload it cannot count could loop.
 */
export function recoverPortalSession(): boolean {
  if (read() !== false) return false;
  try {
    window.sessionStorage.setItem(MARK, "1");
  } catch {
    return false;
  }
  page.reload();
  return true;
}

/** The portal let a call through: the next 401 of the portal may reload again. */
export function confirmPortalSession(): void {
  try {
    window.sessionStorage.removeItem(MARK);
  } catch {
    // Nothing was marked if nothing can be stored.
  }
}
