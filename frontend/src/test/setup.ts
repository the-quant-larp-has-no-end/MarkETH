import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { toast } from "sonner";
import { afterEach, beforeEach, vi } from "vitest";

import { verifyApiMock } from "./http";
import { installMatchMedia, resetMedia } from "./media";
import { resetNavigation } from "./navigation";
import { installObservers, resetObservers } from "./observers";

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
  resetMedia();
  resetNavigation();
  resetObservers();
  window.sessionStorage.clear();
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  // A toast that is still up belongs to the test that raised it: sonner keeps its toasts
  // outside React and would hand them to the next test's Toaster.
  toast.dismiss();
  vi.unstubAllGlobals();
  // Last: a request without a route fails the test that made it.
  verifyApiMock();
});
