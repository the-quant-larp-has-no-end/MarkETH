/**
 * What the runner of the browser suite says, in environment variables. This file and
 * `playwright.config.ts` are the only places of the suite that read one.
 *
 * | Variable          | Says                                                        | Left out               |
 * | ----------------- | ----------------------------------------------------------- | ---------------------- |
 * | `E2E_BASE_URL`    | Where the stack under test answers                          | `http://localhost:8080` |
 * | `E2E_IDENTITY`    | How the stack learns who is calling: `cookies` or `headers` | `cookies`              |
 * | `E2E_SCREENSHOTS` | The directory the screenshots of the screens go into        | `test-results/screens` |
 * | `E2E_RUN`         | What the made-up people of this run are named after         | made from the clock    |
 *
 * `cookies` is the development stack, whose proxy names the caller from the cookies
 * `dev_user_id` and `dev_user_name` (infra/nginx/dev). `headers` is a stack of
 * `compose.yaml` alone, as it is deployed: its proxy names nobody and hands on the two
 * headers of the portal, so the suite sends them itself.
 */

const IDENTITIES = ["cookies", "headers"] as const;

export type IdentityMode = (typeof IDENTITIES)[number];

function identityMode(): IdentityMode {
  const said = process.env.E2E_IDENTITY ?? "cookies";
  const mode = IDENTITIES.find((known) => known === said);
  if (mode === undefined) {
    throw new Error(`E2E_IDENTITY is "${said}". It is one of: ${IDENTITIES.join(", ")}.`);
  }
  return mode;
}

/**
 * The run's own name: lower-case letters and digits, eight at most, so that the username
 * of a made-up person (`e2e_<run>_<n>`) stays within the twenty characters a person may
 * choose. The process that loads the configuration makes it and writes it into its
 * environment, which the workers inherit, so every worker of a run reads the same one.
 */
function runName(): string {
  const said = (process.env.E2E_RUN ??= Date.now().toString(36));
  if (!/^[a-z0-9]{1,8}$/.test(said)) {
    throw new Error(`E2E_RUN is "${said}". It is 1 to 8 lower-case letters and digits.`);
  }
  return said;
}

/** No trailing slash: the origin a browser sends for a page of the stack. */
export const BASE_URL = (process.env.E2E_BASE_URL ?? "http://localhost:8080").replace(/\/+$/, "");
export const IDENTITY = identityMode();
export const RUN = runName();
/** Set when the runner names a directory; otherwise the screenshots lie in the output directory of the run. */
export const SCREENSHOTS: string | undefined = process.env.E2E_SCREENSHOTS || undefined;
