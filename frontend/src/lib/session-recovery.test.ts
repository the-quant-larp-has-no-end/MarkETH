import { beforeEach, expect, test, vi } from "vitest";

import { page } from "./browser";
import { confirmPortalSession, recoverPortalSession } from "./session-recovery";

beforeEach(() => {
  vi.spyOn(page, "reload").mockImplementation(() => {});
});

test("the portal's 401 reloads the page once", () => {
  expect(recoverPortalSession()).toBe(true);
  expect(page.reload).toHaveBeenCalledTimes(1);
});

test("the same answer after the reload does not reload again", () => {
  recoverPortalSession();

  expect(recoverPortalSession()).toBe(false);
  expect(recoverPortalSession()).toBe(false);
  expect(page.reload).toHaveBeenCalledTimes(1);
});

test("once the portal let a call through, a later 401 may reload again", () => {
  recoverPortalSession();
  confirmPortalSession();

  expect(recoverPortalSession()).toBe(true);
  expect(page.reload).toHaveBeenCalledTimes(2);
});

test("without session storage nothing reloads, because it could not be counted", () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new DOMException("denied", "SecurityError");
  });

  expect(recoverPortalSession()).toBe(false);
  expect(page.reload).not.toHaveBeenCalled();
  expect(() => confirmPortalSession()).not.toThrow();
});

test("the mark is the only thing the app keeps in the browser, and it is no code", () => {
  recoverPortalSession();

  expect(Object.keys(window.sessionStorage)).toEqual(["marketh.reloaded"]);
  expect(window.localStorage).toHaveLength(0);
});
