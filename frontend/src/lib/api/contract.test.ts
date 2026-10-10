import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, test } from "vitest";

/**
 * The frontend is built against `backend/openapi.json`. These tests fail when the
 * generated types are behind that file (run `make api-client`), and when the contract
 * gains an operation that `lib/api` has no query or mutation for.
 */

const FRONTEND = process.cwd();
const CONTRACT = join(FRONTEND, "../backend/openapi.json");
const read = (path: string) => readFileSync(join(FRONTEND, path), "utf8");

type Operation = { path: string; method: string; operationId: string };
type Contract = {
  paths: Record<string, Record<string, { operationId?: string }>>;
  components: { schemas: Record<string, unknown> };
};

const METHODS = ["get", "put", "post", "delete", "patch"];

function load(): { contract: Contract; operations: Operation[] } {
  const contract = JSON.parse(readFileSync(CONTRACT, "utf8")) as Contract;
  const operations = Object.entries(contract.paths).flatMap(([path, item]) =>
    METHODS.filter((method) => method in item).map((method) => ({
      path,
      method,
      operationId: item[method]?.operationId ?? "",
    })),
  );
  return { contract, operations };
}

/** Operations no screen calls (DESIGN.md §13), and the stream, which is an EventSource. */
const NOT_CALLED = new Set(["get_health", "run_monthly_grants", "stream_market"]);

// The contract lies beside the frontend in the repository; an image build has only frontend/.
describe.skipIf(!existsSync(CONTRACT))("the generated types match backend/openapi.json", () => {
  const schema = read("src/lib/api/schema.d.ts");

  test("every path of the contract is in schema.d.ts, and no other", () => {
    const { contract } = load();
    const generated = [...schema.matchAll(/^ {4}"(\/api\/[^"]*)": \{$/gm)].map((match) => match[1]);

    expect(generated.sort()).toEqual(Object.keys(contract.paths).sort());
  });

  test("every operation of the contract is in schema.d.ts", () => {
    const { operations } = load();

    const missing = operations
      .map((operation) => operation.operationId)
      .filter((id) => !new RegExp(`^ {4}${id}: \\{$`, "m").test(schema));
    expect(missing).toEqual([]);
  });

  test("every schema of the contract has its root type", () => {
    const { contract } = load();

    const missing = Object.keys(contract.components.schemas).filter(
      (name) => !schema.includes(`export type ${name} = components['schemas']['${name}'];`),
    );
    expect(missing).toEqual([]);
  });
});

describe.skipIf(!existsSync(CONTRACT))("lib/api covers the contract", () => {
  const queries = read("src/lib/api/queries.ts");
  const mutations = read("src/lib/api/mutations.ts");
  const stream = read("src/lib/live/market-stream.ts");

  test("every read has its query", () => {
    const { operations } = load();

    const missing = operations
      .filter((operation) => operation.method === "get" && !NOT_CALLED.has(operation.operationId))
      .filter((operation) => !queries.includes(`api.GET("${operation.path}"`))
      .map((operation) => `GET ${operation.path}`);
    expect(missing).toEqual([]);
  });

  test("every write has its mutation, and with it its row in the invalidation table", () => {
    const { operations } = load();

    const missing = operations
      .filter((operation) => operation.method !== "get" && !NOT_CALLED.has(operation.operationId))
      .filter(
        (operation) =>
          !mutations.includes(`api.${operation.method.toUpperCase()}("${operation.path}"`),
      )
      .map((operation) => `${operation.method.toUpperCase()} ${operation.path}`);
    expect(missing).toEqual([]);
  });

  test("the stream is followed by the live helper, at the contract's path", () => {
    const { operations } = load();
    const path = operations.find((operation) => operation.operationId === "stream_market")?.path;

    expect(path).toBe("/api/markets/{market_id}/stream");
    expect(stream).toContain("/api/markets/${encodeURIComponent(marketId)}/stream");
  });

  test("no query sets a page size: a list is as long as the API makes it", () => {
    expect(queries).not.toMatch(/\blimit\b\s*:/);
  });
});
