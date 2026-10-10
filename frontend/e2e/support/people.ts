import type { Browser, BrowserContext } from "@playwright/test";

import type { PublicConfigResponse, SaveProfileRequest } from "../../src/lib/api/schema";

import { BASE_URL, IDENTITY, RUN } from "./runner";

/**
 * A person in a spec is a browser context: every request of it is that person's, as if
 * the portal had signed them in. Several people are several contexts.
 */
export type Person = {
  /** The id the portal sends: an address at `ALLOWED_EMAIL_DOMAINS`. */
  id: string;
  /** The display name the portal sends. */
  name: string;
};

/** The degree a person states in their profile: a pair `degrees` of `GET /config` lists. */
export type Degree = Pick<SaveProfileRequest, "degree_level" | "degree_programme">;

/**
 * The demo students of `backend/scripts/seed.py`. Each has a profile on a seeded stack.
 * A spec reads what the seed left them and writes nothing as them.
 */
export const STUDENTS = {
  anna: { id: "demo.anna@ethz.ch", name: "Anna Demo" },
  ben: { id: "demo.ben@ethz.ch", name: "Ben Demo" },
  chiara: { id: "demo.chiara@ethz.ch", name: "Chiara Demo" },
  dario: { id: "demo.dario@ethz.ch", name: "Dario Demo" },
  elif: { id: "demo.elif@ethz.ch", name: "Elif Demo" },
  finn: { id: "demo.finn@ethz.ch", name: "Finn Demo" },
} as const satisfies Record<string, Person>;

/** The two request headers in which the portal names the caller (SPEC §3). */
const HEADER_USER_ID = "X-User-Id";
const HEADER_USER_NAME = "X-User-Name";

/** A worker makes up at most this many people; the next worker counts on from there. */
const PEOPLE_PER_WORKER = 1000;

let madeUp = 0;

/**
 * A browser context as `person`. The name travels percent-encoded, as the portal sends
 * it and as the page at `/__dev/identity` stores it.
 */
export async function contextAs(browser: Browser, person: Person): Promise<BrowserContext> {
  const name = encodeURIComponent(person.name);
  if (IDENTITY === "headers") {
    return browser.newContext({
      extraHTTPHeaders: { [HEADER_USER_ID]: person.id, [HEADER_USER_NAME]: name },
    });
  }
  const context = await browser.newContext();
  await context.addCookies([
    { name: "dev_user_id", value: person.id, url: BASE_URL },
    { name: "dev_user_name", value: name, url: BASE_URL },
  ]);
  return context;
}

/**
 * The next made-up person of this run: `e2e.<run>.<n>@ethz.ch`. `n` is counted per
 * worker and starts where no other worker of the run counts, so an address is made up
 * once.
 */
export function makeUpPerson(workerIndex: number): Person & { username: string } {
  madeUp += 1;
  if (madeUp >= PEOPLE_PER_WORKER) {
    throw new Error(`One worker makes up fewer than ${PEOPLE_PER_WORKER} people.`);
  }
  const n = workerIndex * PEOPLE_PER_WORKER + madeUp;
  return {
    id: `e2e.${RUN}.${n}@ethz.ch`,
    name: `E2E Person ${n}`,
    username: `e2e_${RUN}_${n}`,
  };
}

/**
 * The person of `context` sets up their profile, with the request the set-up screen
 * sends: `PUT /me/profile` with the usage policy accepted. Without a `degree` it is the
 * first programme of the first level the stack offers.
 */
export async function setUpProfile(
  context: BrowserContext,
  username: string,
  degree?: Degree,
): Promise<void> {
  let stated = degree;
  if (stated === undefined) {
    const answer = await context.request.get(`${BASE_URL}/api/config`);
    if (!answer.ok()) {
      throw new Error(`GET /api/config answered ${answer.status()}: ${await answer.text()}`);
    }
    const config: PublicConfigResponse = await answer.json();
    const level = config.degrees.find((option) => option.programmes.length > 0);
    if (level === undefined) throw new Error("GET /api/config offers no degree programme.");
    stated = { degree_level: level.level, degree_programme: level.programmes[0] };
  }
  const profile: SaveProfileRequest = { username, ...stated, policy_accepted: true };
  const saved = await context.request.put(`${BASE_URL}/api/me/profile`, {
    // The API takes a request that changes something only from its own pages (SPEC A.2).
    headers: { Origin: new URL(BASE_URL).origin },
    data: profile,
  });
  if (!saved.ok()) {
    throw new Error(
      `PUT /api/me/profile as ${username} answered ${saved.status()}: ${await saved.text()}`,
    );
  }
}
