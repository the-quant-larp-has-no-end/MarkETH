import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";

import { mockApi, verifyApiMock } from "./http";
import { installMatchMedia, resetMedia } from "./media";
import { resetNavigation } from "./navigation";
import { installObservers, resetObservers } from "./observers";
import { clearToasts } from "./toasts";

/* The router is the in-memory one of ./navigation, in every test. */
vi.mock("next/navigation", async () => (await import("./navigation")).nextNavigationModule);
vi.mock("next/link", async () => (await import("./navigation")).nextLinkModule);

/* What a browser has and jsdom has not. */
installMatchMedia();
installObservers();
window.scrollTo = () => {};
Element.prototype.scrollIntoView ??= () => {};
Element.prototype.hasPointerCapture ??= () => false;
Element.prototype.setPointerCapture ??= () => {};
Element.prototype.releasePointerCapture ??= () => {};

beforeEach(() => {
  // Until the test names its routes there is none: a request fails the test that made it
  // and never leaves for the network.
  mockApi();
  resetMedia();
  resetNavigation();
  resetObservers();
  window.sessionStorage.clear();
  window.localStorage.clear();
});

afterEach(async () => {
  cleanup();
  // A toast belongs to the test that raised it, and so does the news that it was put
  // away: sonner keeps both outside React and would hand them to the next test's Toaster.
  await clearToasts();
  vi.unstubAllGlobals();
  // Last: a request without a route fails the test that made it.
  verifyApiMock();
});
