import { existsSync, readdirSync, readFileSync } from "node:fs";
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

type Parameter = { name: string; in: string; required?: boolean };
type Operation = {
  path: string;
  method: string;
  operationId: string;
  parameters: Parameter[];
  /** The operation answers with an event stream. */
  stream: boolean;
};
type Contract = {
  paths: Record<
    string,
    Record<
      string,
      {
        operationId?: string;
        parameters?: Parameter[];
        responses?: Record<string, { content?: Record<string, unknown> }>;
      }
    >
  >;
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
      parameters: item[method]?.parameters ?? [],
      stream: "text/event-stream" in (item[method]?.responses?.["200"]?.content ?? {}),
    })),
  );
  return { contract, operations };
}

/**
 * Operations the app does not call through the client: the two no screen calls
 * (DESIGN.md §13), and the event streams, which `lib/live` opens as an `EventSource`.
 */
const NOT_CALLED = new Set(["get_health", "run_monthly_grants", "stream_live", "stream_market"]);

/** The code of the app: every file under `src/` but the tests, their helpers and the generated types. */
function appFiles(): { path: string; text: string }[] {
  return (readdirSync(join(FRONTEND, "src"), { recursive: true }) as string[])
    .map((name) => `src/${name.split("\\").join("/")}`)
    .filter((path) => /\.tsx?$/.test(path))
    .filter((path) => !/\.test\.tsx?$/.test(path) && !path.startsWith("src/test/"))
    .filter((path) => path !== "src/lib/api/schema.d.ts")
    .map((path) => ({ path, text: read(path) }));
}

/** A path of the contract as code writes it: `{market_id}` stands there as `${...}`. */
function addressOf(path: string): RegExp {
  const written = path
    .split(/\{[^}]+\}/)
    .map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
    .join("\\$\\{[^}]*\\}");
  return new RegExp(`${written}(?![\\w/-])`);
}

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

  test("every mutation is named in the invalidation table at the top of mutations.ts", () => {
    // The table is the comment before the first helper: what each write does to the cache.
    const table = mutations.slice(0, mutations.indexOf("function refresh("));
    const exported = [...mutations.matchAll(/^export const (\w+Mutation) = /gm)].map(
      (match) => match[1]!,
    );
    // The seven admin actions on a market share one row.
    const marketActions = exported.filter((name) =>
      mutations.includes(`${name} = mutationOptions({\n  mutationFn: ({ marketId, body }`),
    );

    expect(exported.length).toBeGreaterThan(20);
    expect(marketActions).toHaveLength(7);
    expect(table).toContain("the seven market actions");
    const missing = exported.filter(
      (name) => !marketActions.includes(name) && !table.includes(`\`${name}\``),
    );
    expect(missing).toEqual([]);
  });

  test("every write that takes an Idempotency-Key goes out as a stored body with its stored key", () => {
    const { operations } = load();
    const keyed = operations.filter((operation) =>
      operation.parameters.some(
        (parameter) => parameter.in === "header" && parameter.name === "Idempotency-Key",
      ),
    );

    // The trade and the adjustment of a user's tokens (DESIGN.md §14).
    expect(keyed.map((operation) => operation.path).sort()).toEqual([
      "/api/admin/users/{user_id}/adjustments",
      "/api/markets/{market_id}/trades",
    ]);
    for (const operation of keyed) {
      const from = mutations.indexOf(`api.POST("${operation.path}"`);
      const call = mutations.slice(from, mutations.indexOf("}),", from));
      // The key of the submission, and its body as it was serialised on the confirming tap.
      expect(call, operation.path).toContain('header: { "Idempotency-Key": idempotencyKey }');
      expect(call, operation.path).toContain("...stored(body)");
    }
    // Both are sent by the one store that keeps a body and its key; nothing else is.
    expect(mutations.match(/\.\.\.stored\(body\)/g)).toHaveLength(keyed.length);
    expect(mutations.match(/"Idempotency-Key"/g)).toHaveLength(keyed.length);
    expect(mutations).not.toContain("newUuid");
  });

  test("an event stream is opened, not called: one file of lib/live names its path, and no other file does", () => {
    const { operations } = load();
    const streams = operations.filter((operation) => operation.stream);
    const app = appFiles();

    expect(streams.length).toBeGreaterThan(0);
    for (const stream of streams) {
      const named = `${stream.operationId} at ${stream.path}`;
      expect(stream.method, named).toBe("get");
      expect(NOT_CALLED.has(stream.operationId), named).toBe(true);
      expect(queries, named).not.toContain(`api.GET("${stream.path}"`);

      const address = addressOf(stream.path);
      const naming = app.filter((file) => address.test(file.text)).map((file) => file.path);
      expect(naming, named).toHaveLength(1);
      expect(naming[0], named).toMatch(/^src\/lib\/live\//);
    }
  });

  test("no query sets a page size: a list is as long as the API makes it", () => {
    expect(queries).not.toMatch(/\blimit\b\s*:/);
  });
});
