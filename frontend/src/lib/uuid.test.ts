import { expect, test, vi } from "vitest";

import { newUuid } from "./uuid";

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

test("a key is a version 4 UUID, and no two are alike", () => {
  const first = newUuid();
  const second = newUuid();

  expect(first).toMatch(UUID_V4);
  expect(second).toMatch(UUID_V4);
  expect(first).not.toBe(second);
});

test("where the page is not served securely there is no randomUUID, and a key is still made", () => {
  const real = globalThis.crypto;
  vi.stubGlobal("crypto", {
    getRandomValues: <T extends ArrayBufferView>(array: T) => real.getRandomValues(array),
  });

  const made = new Set(Array.from({ length: 50 }, () => newUuid()));

  expect(made.size).toBe(50);
  for (const key of made) expect(key).toMatch(UUID_V4);
});
