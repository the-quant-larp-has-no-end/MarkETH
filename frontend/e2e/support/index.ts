/**
 * What a spec of the browser suite imports: `test` with the people of the spec, and the
 * helpers every spec ends with.
 *
 *   import { expect, expectLayoutHolds, screenshot, STUDENTS, test } from "./support";
 *
 *   test("a seeded student sees Markets", async ({ people }, testInfo) => {
 *     const anna = await people.as(STUDENTS.anna);
 *     const page = await anna.newPage();
 *     await page.goto("/");
 *     …
 *     await expectLayoutHolds(page);
 *     await screenshot(page, testInfo, "markets");
 *   });
 *
 * A spec changes only people and markets it made itself, and reads the seeded ones, so
 * it passes again on the same database.
 */
import { test as base, type BrowserContext } from "@playwright/test";

import { contextAs, type Degree, makeUpPerson, type Person, setUpProfile } from "./people";

export { expect } from "@playwright/test";
export { expectLayoutHolds, type LayoutOptions } from "./layout";
export { type Degree, type Person, STUDENTS } from "./people";
export { BASE_URL, IDENTITY, RUN } from "./runner";
export { screenshot } from "./screens";

/** A made-up person with the browser context that is theirs. */
export type Newcomer = { person: Person; username: string; context: BrowserContext };

export type People = {
  /** A browser context of its own in which every request is `person`'s. */
  as(person: Person): Promise<BrowserContext>;
  /**
   * A person nobody has seen: `e2e.<run>.<n>@ethz.ch`, who sets up their own profile, in
   * the programme `degree` names or in the first one the stack offers. The stack gives
   * them what it gives everybody who signs up.
   */
  join(degree?: Degree): Promise<Newcomer>;
};

export const test = base.extend<{ people: People }>({
  // The second parameter hands the fixture to the test. It is not named `use`, which the
  // lint rules of React take for a hook.
  people: async ({ browser }, provide, testInfo) => {
    const opened: BrowserContext[] = [];
    const as = async (person: Person) => {
      const context = await contextAs(browser, person);
      opened.push(context);
      return context;
    };
    await provide({
      as,
      join: async (degree) => {
        const { username, ...person } = makeUpPerson(testInfo.workerIndex);
        const context = await as(person);
        await setUpProfile(context, username, degree);
        return { person, username, context };
      },
    });
    await Promise.all(opened.map((context) => context.close()));
  },
});
