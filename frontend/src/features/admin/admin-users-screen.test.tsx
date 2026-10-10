import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, test } from "vitest";

import {
  adminUser,
  adminUsers,
  balanceAdjusted,
  me,
  OTHER_USER_ID,
  USER_ID,
} from "@/test/fixtures";
import {
  apiError,
  deferred,
  type Handler,
  json,
  mockApi,
  type MockRequest,
  noAnswer,
  type Route,
} from "@/test/http";
import { navigation } from "@/test/navigation";
import { intersect } from "@/test/observers";
import { createTestQueryClient, renderWithProviders } from "@/test/render";

import { AdminUsersScreen } from "./admin-users-screen";

const USERS: Route = "GET /api/admin/users";
const SUSPEND: Route = "POST /api/admin/users/{user_id}/suspend";
const REINSTATE: Route = "POST /api/admin/users/{user_id}/reinstate";
const ROLE: Route = "POST /api/admin/users/{user_id}/role";
const ADJUST: Route = "POST /api/admin/users/{user_id}/adjustments";

const BOOTSTRAP_NOTE =
  "This admin is named in the server's configuration. The role cannot be changed and the account cannot be suspended here.";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/** The admin who is looking is `anna_m`, the second row of `adminUsers()`. */
const admin = { me: me({ role: "ADMIN" }) };
type User = ReturnType<typeof renderWithProviders>["user"];

/** A page of users with `first` in the place of the player the fixtures list first. */
const withPlayer = (first: ReturnType<typeof adminUser>) =>
  adminUsers({ items: [first, adminUsers().items[1]!] });

function show(routes: Partial<Record<Route, Handler>> = {}) {
  const api = mockApi({
    [USERS]: json(200, adminUsers()),
    // An answer that says the caller may not do something makes the app ask who is calling.
    "GET /api/me": json(200, admin.me),
    ...routes,
  });
  const rendered = renderWithProviders(<AdminUsersScreen />, admin);
  return { api, ...rendered };
}

const table = () => screen.findByRole("table", { name: "Users" });
const queries = (requests: MockRequest[]) => requests.map((request) => request.query.toString());
const rowOf = async (username: string) =>
  within(await table())
    .getAllByRole("row")
    .find((row) => row.textContent?.includes(username))!;
/** What a screen reader hears of a cell: each of its two lines on its own. */
function spoken(cell: HTMLElement): string {
  const copy = cell.cloneNode(true) as HTMLElement;
  for (const hidden of copy.querySelectorAll("[aria-hidden]")) hidden.remove();
  for (const line of copy.querySelectorAll(".block")) line.append(" ");
  return (copy.textContent ?? "").replace(/\s+/g, " ").trim();
}

/** Opens the dialog of a user through the button of their row. */
async function openUser(user: User, username = "cab_coffee") {
  const row = await rowOf(username);
  await user.click(within(row).getByRole("button", { name: new RegExp(`^${username}`) }));
  return screen.findByRole("dialog", { name: username });
}

async function openStep(user: User, action: string, username = "cab_coffee") {
  const dialog = await openUser(user, username);
  await user.click(within(dialog).getByRole("button", { name: action }));
  return dialog;
}

const gone = () => waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
const actions = (dialog: HTMLElement) =>
  [...dialog.querySelectorAll("[data-slot=user-actions] button")].map(
    (button) => button.textContent,
  );
/** The value that stands beside a label of the facts. */
function fact(dialog: HTMLElement, term: string): string {
  const label = within(dialog)
    .getAllByText(term)
    .find((element) => element.tagName === "DT");
  return spoken(label!.parentElement!.querySelector("dd")!);
}
/** Two submit events before the dialog has drawn anything in between. */
function submitTwiceAtOnce(dialog: HTMLElement) {
  const form = dialog.querySelector("form")!;
  act(() => {
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
}

beforeEach(() => {
  navigation.go("/admin/users");
});

describe("Admin: users", () => {
  test("stands under the tabs as the fourth of them, with a hidden h1 that names the screen", async () => {
    show();

    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Users");
    expect(screen.getByRole("link", { name: "Users" })).toHaveAttribute("aria-current", "page");
    expect(await table()).toBeInTheDocument();
  });

  test("lists everyone the API sends, in its order, in six columns with two lines in the first two", async () => {
    show({
      [USERS]: json(
        200,
        adminUsers({
          items: [
            adminUser({ balance_cr: "1250.519999", status: "SUSPENDED", role: "BARISTA" }),
            adminUsers().items[1]!,
          ],
        }),
      ),
    });

    const users = await table();
    expect(
      within(users)
        .getAllByRole("columnheader")
        .map((header) => header.textContent),
    ).toEqual(["Player", "ETH sign-in", "Role", "Status", "Tokens", "Joined"]);
    const [, first, second] = within(users).getAllByRole("row");
    expect(
      within(first!)
        .getAllByRole("cell")
        .map((cell) => spoken(cell)),
    ).toEqual([
      "cab_coffee MSc Data Science",
      "Bea Beispiel bea.beispiel@ethz.ch",
      "Barista",
      "Suspended",
      // Cut after the second decimal, never rounded up.
      "1,250.51 tokens",
      "22 Sep 2026, 12:30",
    ]);
    expect(
      within(second!)
        .getAllByRole("cell")
        .map((cell) => spoken(cell)),
    ).toEqual([
      "anna_mYou BSc Computer Science",
      "Anna Muster anna.muster@ethz.ch",
      "AdminBootstrap",
      "Active",
      "240 tokens",
      "21 Sep 2026, 10:15",
    ]);
    // "Suspended" is a badge in the style of "Paused"; "Active" is plain text.
    expect(within(first!).getByText("Suspended")).toHaveAttribute("data-variant", "outline");
    expect(within(second!).getByText("Active")).not.toHaveAttribute("data-slot", "badge");
    expect(within(second!).getByText("Bootstrap")).toHaveAttribute("data-slot", "badge");
    // The second lines are captions, and each line is cut on its own.
    expect(within(first!).getByText("MSc Data Science")).toHaveClass("type-caption", "truncate");
    expect(within(first!).getByText("bea.beispiel@ethz.ch")).toHaveClass(
      "type-caption",
      "truncate",
    );
  });

  test("the first two columns share the room the others leave, and the figures keep theirs", async () => {
    show();

    const [, row] = within(await table()).getAllByRole("row");
    const cells = within(row!).getAllByRole("cell");
    for (const cell of cells.slice(0, 2)) expect(cell).toHaveClass("w-1/2", "max-w-0", "truncate");
    for (const cell of cells.slice(2)) expect(cell).toHaveClass("w-px", "whitespace-nowrap");
    expect(cells[4]).toHaveClass("text-right", "type-number");
  });

  test("a name the portal never sent is a dash", async () => {
    show({ [USERS]: json(200, withPlayer(adminUser({ name: "" }))) });

    const row = await rowOf("cab_coffee");
    expect(spoken(within(row).getAllByRole("cell")[1]!)).toBe("none yet bea.beispiel@ethz.ch");
  });

  test("the first request names no search, no role, no status and no page size", async () => {
    const { api } = show();
    await table();

    expect(queries(api.calls(USERS))).toEqual([""]);
  });

  test("the search asks a moment after the last keystroke, with what was typed as q", async () => {
    const { api, user } = show();
    await table();
    const search = screen.getByLabelText("Search");
    expect(search).toHaveAttribute("placeholder", "Username, name or email");

    await user.type(search, " bea ");

    // Not with every keystroke: nothing has been asked yet.
    expect(api.calls(USERS)).toHaveLength(1);
    await waitFor(() => expect(api.calls(USERS)).toHaveLength(2));
    // One request for the whole word, without the blanks around it.
    expect(queries(api.calls(USERS))).toEqual(["", "q=bea"]);
  });

  test("an emptied search field sends no q", async () => {
    const { api, user } = show();
    await table();
    await user.type(screen.getByLabelText("Search"), "bea");
    await waitFor(() => expect(api.calls(USERS)).toHaveLength(2));

    await user.clear(screen.getByLabelText("Search"));

    // The list of everybody is in the cache, and is asked for again without a search.
    await waitFor(() => expect(api.calls(USERS)).toHaveLength(3));
    expect(queries(api.calls(USERS))).toEqual(["", "q=bea", ""]);
  });

  test("a select asks as soon as it is changed, and its first option sends nothing", async () => {
    const { api, user } = show();
    await table();
    const role = screen.getByLabelText("Role");
    const status = screen.getByLabelText("Status");
    expect(
      within(role)
        .getAllByRole("option")
        .map((option) => option.textContent),
    ).toEqual(["All roles", "User", "Barista", "Admin"]);
    expect(
      within(status)
        .getAllByRole("option")
        .map((option) => option.textContent),
    ).toEqual(["All statuses", "Active", "Suspended"]);

    await user.selectOptions(role, "Barista");
    await waitFor(() => expect(queries(api.calls(USERS))).toContain("role=BARISTA"));

    await user.selectOptions(status, "Suspended");
    await waitFor(() =>
      expect(queries(api.calls(USERS))).toContain("role=BARISTA&status=SUSPENDED"),
    );

    await user.selectOptions(role, "All roles");
    await waitFor(() => expect(queries(api.calls(USERS))).toContain("status=SUSPENDED"));
    // Changing a select asked the API to list; it changed nobody.
    expect(api.requests.every((request) => request.method === "GET")).toBe(true);
  });

  test("the search and the two selects are sent together", async () => {
    const { api, user } = show();
    await table();

    await user.selectOptions(screen.getByLabelText("Role"), "Admin");
    await user.selectOptions(screen.getByLabelText("Status"), "Active");
    await user.type(screen.getByLabelText("Search"), "ethz");

    await waitFor(() =>
      expect(queries(api.calls(USERS))).toContain("q=ethz&role=ADMIN&status=ACTIVE"),
    );
  });

  test("more rows load with next_cursor when the admin reaches the end of the table", async () => {
    const { api, user } = show({
      [USERS]: (request) =>
        request.query.get("cursor") === "page-2"
          ? json(
              200,
              adminUsers({
                items: [adminUser({ id: "third", username: "third_one" })],
                next_cursor: null,
              }),
            )
          : json(200, adminUsers({ next_cursor: "page-2" })),
    });
    await user.selectOptions(screen.getByLabelText("Status"), "Active");
    await waitFor(() => expect(queries(api.calls(USERS))).toContain("status=ACTIVE"));
    await rowOf("cab_coffee");
    await waitFor(() => expect(document.querySelector("[data-slot=load-more]")).not.toBeNull());

    act(() => intersect());

    await waitFor(() =>
      expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(4),
    );
    // The next page is asked for with the cursor of the last one, and the same choice.
    expect(queries(api.calls(USERS)).at(-1)).toBe("status=ACTIVE&cursor=page-2");
    expect(await rowOf("third_one")).toBeInTheDocument();
    // The last page has no cursor: nothing more is asked for.
    expect(document.querySelector("[data-slot=load-more]")).toBeNull();
  });

  test("shows skeleton rows while the users load, and one plain sentence when nobody matches", async () => {
    const answer = deferred();
    mockApi({ [USERS]: answer.handler });
    renderWithProviders(<AdminUsersScreen />, admin);

    expect(document.querySelector("[data-slot=table-skeleton]")).toBeInTheDocument();
    expect(screen.queryByText("No users match.")).not.toBeInTheDocument();

    answer.resolve(json(200, adminUsers({ items: [] })));

    expect(await screen.findByText("No users match.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  test("users that cannot be read show the API's message with Try again", async () => {
    const api = mockApi({
      [USERS]: apiError(422, "VALIDATION_ERROR", "A search has at most 100 characters.", {
        field: "q",
      }),
    });
    const { user } = renderWithProviders(<AdminUsersScreen />, admin);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "A search has at most 100 characters.",
    );

    api.on(USERS, json(200, adminUsers()));
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await table()).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  test("while another choice is on its way the rows of the last one stay on screen", async () => {
    const answer = deferred();
    const { api, user } = show();
    await rowOf("cab_coffee");
    api.on(USERS, answer.handler);

    await user.selectOptions(screen.getByLabelText("Status"), "Suspended");

    await waitFor(() => expect(api.calls(USERS)).toHaveLength(2));
    expect(screen.getByRole("table", { name: "Users" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Users" })).toHaveAttribute("aria-busy", "true");
    expect(document.querySelector("[data-slot=table-skeleton]")).not.toBeInTheDocument();

    answer.resolve(json(200, adminUsers({ items: [] })));

    expect(await screen.findByText("No users match.")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Users" })).toHaveAttribute("aria-busy", "false");
  });
});

describe("the dialog of a user", () => {
  test("a row opens it: titled with the username, it repeats the facts of the row one under another", async () => {
    const { user } = show();

    const dialog = await openUser(user);

    expect(within(dialog).getByRole("heading", { level: 2, name: "cab_coffee" })).toBeVisible();
    expect(within(dialog).getByRole("button", { name: "Close" })).toBeEnabled();
    expect([...dialog.querySelectorAll("dt")].map((term) => term.textContent)).toEqual([
      "Player",
      "ETH sign-in",
      "Role",
      "Status",
      "Tokens",
      "Joined",
    ]);
    expect(fact(dialog, "Player")).toBe("cab_coffee MSc Data Science");
    expect(fact(dialog, "ETH sign-in")).toBe("Bea Beispiel bea.beispiel@ethz.ch");
    expect(fact(dialog, "Role")).toBe("User");
    expect(fact(dialog, "Status")).toBe("Active");
    expect(fact(dialog, "Tokens")).toBe("980 tokens");
    expect(fact(dialog, "Joined")).toBe("22 Sep 2026, 12:30");
    // An active player who is no bootstrap admin: suspend, change the role, adjust tokens.
    expect(actions(dialog)).toEqual(["Suspend", "Change role", "Adjust tokens"]);
    expect(within(dialog).queryByText(BOOTSTRAP_NOTE)).not.toBeInTheDocument();
    // Nothing was sent by opening it.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  test("a tap anywhere on the row opens it too", async () => {
    const { user } = show();
    const row = await rowOf("cab_coffee");

    await user.click(within(row).getByText("22 Sep 2026, 12:30"));

    expect(await screen.findByRole("dialog", { name: "cab_coffee" })).toBeInTheDocument();
  });

  test("the admin's own row opens nothing and offers nothing", async () => {
    const { api, user } = show();
    const own = await rowOf("anna_m");

    expect(within(own).getByText("You")).toBeInTheDocument();
    expect(within(own).queryByRole("button")).not.toBeInTheDocument();
    await user.click(within(own).getByText("anna_m"));
    await user.click(within(own).getByText("21 Sep 2026, 10:15"));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    // It is the row whose id is the id of /me, whatever else the two have in common.
    expect(admin.me.id).toBe(USER_ID);
    expect(api.requests.every((request) => request.method === "GET")).toBe(true);
  });

  test("a suspended user is offered Reinstate in the place of Suspend", async () => {
    const { user } = show({ [USERS]: json(200, withPlayer(adminUser({ status: "SUSPENDED" }))) });

    const dialog = await openUser(user);

    expect(fact(dialog, "Status")).toBe("Suspended");
    expect(actions(dialog)).toEqual(["Reinstate", "Change role", "Adjust tokens"]);
  });

  test("a bootstrap admin can be neither suspended nor given another role, and the dialog says why", async () => {
    const { user } = show({
      [USERS]: json(200, withPlayer(adminUser({ role: "ADMIN", bootstrap_admin: true }))),
    });

    const dialog = await openUser(user);

    expect(fact(dialog, "Role")).toBe("AdminBootstrap");
    expect(within(dialog).getByText(BOOTSTRAP_NOTE)).toBeInTheDocument();
    expect(actions(dialog)).toEqual(["Adjust tokens"]);
  });

  test("an admin by appointment is offered what a player is", async () => {
    const { user } = show({ [USERS]: json(200, withPlayer(adminUser({ role: "ADMIN" }))) });

    const dialog = await openUser(user);

    expect(fact(dialog, "Role")).toBe("Admin");
    expect(actions(dialog)).toEqual(["Suspend", "Change role", "Adjust tokens"]);
    expect(within(dialog).queryByText(BOOTSTRAP_NOTE)).not.toBeInTheDocument();
  });

  test("the close cross closes it, and the focus goes back to the row", async () => {
    const { user } = show();
    const row = await rowOf("cab_coffee");
    const dialog = await openUser(user);

    await user.click(within(dialog).getByRole("button", { name: "Close" }));

    await gone();
    await waitFor(() => expect(within(row).getByRole("button")).toHaveFocus());
  });

  test("an action puts its step in the place of the facts, and Back returns to them", async () => {
    const { api, user } = show();
    const dialog = await openStep(user, "Change role");

    expect(within(dialog).getByRole("heading", { level: 3, name: "Change role" })).toHaveFocus();
    expect(dialog.querySelector("[data-slot=user-actions]")).not.toBeInTheDocument();
    expect(within(dialog).queryByText("ETH sign-in")).not.toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Back" }));

    expect(fact(dialog, "ETH sign-in")).toBe("Bea Beispiel bea.beispiel@ethz.ch");
    await waitFor(() =>
      expect(within(dialog).getByRole("button", { name: "Change role" })).toHaveFocus(),
    );
    expect(api.requests.every((request) => request.method === "GET")).toBe(true);
  });
});

describe("Suspend and Reinstate", () => {
  const suspended = () => adminUser({ status: "SUSPENDED" });

  test("Suspend states the consequence, asks for a reason and goes ahead on a destructive button", async () => {
    const { user } = show();
    const dialog = await openStep(user, "Suspend");

    expect(
      within(dialog).getByText(
        "cab_coffee is locked out of MarkETH until an admin reinstates the account. Nothing is taken from it: its tokens, positions and vouchers are kept.",
      ),
    ).toBeInTheDocument();
    const reason = within(dialog).getByLabelText("Reason");
    expect(reason).toHaveAccessibleDescription("Kept in the audit log.");
    const go = within(dialog).getByRole("button", { name: "Suspend" });
    expect(go).toHaveAttribute("data-variant", "destructive");
    // The reason is required, and blanks are no reason.
    expect(go).toBeDisabled();
    await user.type(reason, "   ");
    expect(go).toBeDisabled();
    await user.type(reason, "Traded a market they settle.");
    expect(go).toBeEnabled();
  });

  test("sends the reason; the answer closes the dialog, says so in a toast and is what the row shows", async () => {
    const { api, user } = show({ [SUSPEND]: json(200, suspended()) });
    const dialog = await openStep(user, "Suspend");
    api.on(USERS, json(200, withPlayer(suspended())));

    await user.type(within(dialog).getByLabelText("Reason"), " Traded a market they settle. ");
    await user.click(within(dialog).getByRole("button", { name: "Suspend" }));

    await gone();
    expect(api.lastCall(SUSPEND)?.path).toBe(`/api/admin/users/${OTHER_USER_ID}/suspend`);
    expect(api.lastCall(SUSPEND)?.body).toEqual({ reason: "Traded a market they settle." });
    expect(await screen.findByText("Account suspended")).toBeInTheDocument();
    const row = await rowOf("cab_coffee");
    await waitFor(() =>
      expect(within(row).getByText("Suspended")).toHaveAttribute("data-slot", "badge"),
    );
  });

  test("while the step of a suspension is shown a tap beside the dialog does not close it; beside the facts it does", async () => {
    const { user } = show();
    const dialog = await openStep(user, "Suspend");
    const scrim = () => document.querySelector<HTMLElement>("[data-slot=dialog-overlay]")!;

    await user.click(scrim());

    expect(screen.getByRole("dialog", { name: "cab_coffee" })).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Reason")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Back" }));
    await user.click(scrim());

    await gone();
  });

  test("Reinstate says what it does, asks for a reason and goes ahead on the primary button", async () => {
    const { api, user } = show({
      [USERS]: json(200, withPlayer(suspended())),
      [REINSTATE]: json(200, adminUser()),
    });
    const dialog = await openStep(user, "Reinstate");

    expect(within(dialog).getByText("cab_coffee can use MarkETH again.")).toBeInTheDocument();
    const go = within(dialog).getByRole("button", { name: "Reinstate" });
    expect(go).toHaveAttribute("data-variant", "primary");
    expect(go).toBeDisabled();
    api.on(USERS, json(200, adminUsers()));

    await user.type(within(dialog).getByLabelText("Reason"), "Cleared with the board.");
    await user.click(go);

    await gone();
    expect(api.lastCall(REINSTATE)?.path).toBe(`/api/admin/users/${OTHER_USER_ID}/reinstate`);
    expect(api.lastCall(REINSTATE)?.body).toEqual({ reason: "Cleared with the board." });
    expect(await screen.findByText("Account reinstated")).toBeInTheDocument();
    const row = await rowOf("cab_coffee");
    await waitFor(() => expect(within(row).queryByText("Suspended")).not.toBeInTheDocument());
  });

  test.each([
    [
      "FORBIDDEN",
      403,
      "This account is an admin by the server's configuration. It cannot be suspended.",
    ],
    ["CONFLICT", 409, "This account is already suspended."],
    ["NOT_FOUND", 404, "Not found."],
  ] as const)(
    "%s shows the API's message above the button, and the dialog stays",
    async (code, status, message) => {
      const { user } = show({ [SUSPEND]: apiError(status, code, message) });
      const dialog = await openStep(user, "Suspend");

      await user.type(within(dialog).getByLabelText("Reason"), "Traded a market they settle.");
      await user.click(within(dialog).getByRole("button", { name: "Suspend" }));

      expect(await within(dialog).findByRole("alert")).toHaveTextContent(message);
      expect(screen.getByRole("dialog", { name: "cab_coffee" })).toBeInTheDocument();
      expect(within(dialog).getByLabelText("Reason")).toHaveValue("Traded a market they settle.");
      expect(within(dialog).getByLabelText("Reason")).not.toBeInvalid();
      // An answer is about what was sent: it goes when the reason changes.
      await user.type(within(dialog).getByLabelText("Reason"), " Again.");
      expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
    },
  );

  test("a reason the API does not take is said on its field", async () => {
    const { user } = show({
      [SUSPEND]: apiError(422, "VALIDATION_ERROR", "A reason has at most 500 characters.", {
        field: "reason",
      }),
    });
    const dialog = await openStep(user, "Suspend");

    await user.type(within(dialog).getByLabelText("Reason"), "Too long.");
    await user.click(within(dialog).getByRole("button", { name: "Suspend" }));

    const reason = within(dialog).getByLabelText("Reason");
    await waitFor(() => expect(reason).toBeInvalid());
    expect(reason).toHaveAccessibleDescription(/A reason has at most 500 characters\./);
  });

  test("from tap until the answer the dialog cannot be closed, and two taps are one suspension", async () => {
    const answer = deferred();
    const { api, user } = show({ [SUSPEND]: answer.handler });
    const dialog = await openStep(user, "Suspend");

    await user.type(within(dialog).getByLabelText("Reason"), "Traded a market they settle.");
    await user.click(within(dialog).getByRole("button", { name: "Suspend" }));
    await user.click(within(dialog).getByRole("button", { name: "Suspend" }));
    await user.keyboard("{Escape}");

    expect(within(dialog).getByRole("button", { name: "Suspend" })).toHaveAttribute(
      "aria-busy",
      "true",
    );
    expect(within(dialog).getByRole("button", { name: "Back" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Close" })).toBeDisabled();
    expect(screen.getByRole("dialog", { name: "cab_coffee" })).toBeInTheDocument();
    expect(api.calls(SUSPEND)).toHaveLength(1);

    answer.resolve(json(200, suspended()));
    await gone();
    expect(api.calls(SUSPEND)).toHaveLength(1);
  });

  test("two submits in one moment are one suspension: the second is stopped where it is made", async () => {
    const answer = deferred();
    const { api, user } = show({ [SUSPEND]: answer.handler });
    const dialog = await openStep(user, "Suspend");
    await user.type(within(dialog).getByLabelText("Reason"), "Traded a market they settle.");

    submitTwiceAtOnce(dialog);

    await waitFor(() => expect(api.calls(SUSPEND)).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(api.calls(SUSPEND)).toHaveLength(1);
  });

  test("without an answer the users are read again before the button comes back, and the dialog shows what the server holds", async () => {
    const reread = deferred();
    const { api, user } = show();
    api.on(SUSPEND, noAnswer);
    const dialog = await openStep(user, "Suspend");
    api.on(USERS, reread.handler);

    await user.type(within(dialog).getByLabelText("Reason"), "Traded a market they settle.");
    await user.click(within(dialog).getByRole("button", { name: "Suspend" }));

    await waitFor(() => expect(api.calls(USERS)).toHaveLength(2));
    expect(within(dialog).getByRole("button", { name: "Suspend" })).toHaveAttribute(
      "aria-busy",
      "true",
    );

    // The suspension did arrive: the list says so, and the step it belonged to is over.
    reread.resolve(json(200, withPlayer(suspended())));

    await waitFor(() => expect(fact(dialog, "Status")).toBe("Suspended"));
    expect(actions(dialog)).toEqual(["Reinstate", "Change role", "Adjust tokens"]);
    expect(api.calls(SUSPEND)).toHaveLength(1);
  });

  test("without an answer and with the account as it was, the step says so and can be sent again", async () => {
    const { api, user } = show();
    api.on(SUSPEND, noAnswer);
    const dialog = await openStep(user, "Suspend");

    await user.type(within(dialog).getByLabelText("Reason"), "Traded a market they settle.");
    await user.click(within(dialog).getByRole("button", { name: "Suspend" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("No connection. Try again.");
    expect(api.calls(USERS)).toHaveLength(2);
    expect(within(dialog).getByRole("button", { name: "Suspend" })).toBeEnabled();
  });
});

describe("Change role", () => {
  test("shows the select, set to the user's role, with one line that follows the choice", async () => {
    const { api, user } = show();
    const dialog = await openStep(user, "Change role");
    const select = within(dialog).getByLabelText("Role");

    expect(
      within(select)
        .getAllByRole("option")
        .map((option) => [option.textContent, option.getAttribute("value")]),
    ).toEqual([
      ["User", "USER"],
      ["Barista", "BARISTA"],
      ["Admin", "ADMIN"],
    ]);
    expect(select).toHaveValue("USER");
    expect(select).toHaveAccessibleDescription("Trades and proposes markets.");
    // Nothing to save while the role is the one the user has.
    expect(within(dialog).getByRole("button", { name: "Save role" })).toBeDisabled();

    await user.selectOptions(select, "Barista");
    expect(select).toHaveAccessibleDescription("Also scans vouchers at the bar.");
    expect(within(dialog).getByRole("button", { name: "Save role" })).toBeEnabled();

    await user.selectOptions(select, "Admin");
    expect(select).toHaveAccessibleDescription(
      "Also runs the admin area: markets, codes, risk and users.",
    );

    await user.selectOptions(select, "User");
    expect(within(dialog).getByRole("button", { name: "Save role" })).toBeDisabled();
    // The select posts nothing by itself.
    expect(api.calls(ROLE)).toHaveLength(0);
  });

  test("Save role sends the role; the answer closes the dialog, says so in a toast and is what the row shows", async () => {
    const barista = adminUser({ role: "BARISTA" });
    const { api, user } = show({ [ROLE]: json(200, barista) });
    const dialog = await openStep(user, "Change role");
    api.on(USERS, json(200, withPlayer(barista)));

    await user.selectOptions(within(dialog).getByLabelText("Role"), "Barista");
    await user.click(within(dialog).getByRole("button", { name: "Save role" }));

    await gone();
    expect(api.lastCall(ROLE)?.path).toBe(`/api/admin/users/${OTHER_USER_ID}/role`);
    expect(api.lastCall(ROLE)?.body).toEqual({ role: "BARISTA" });
    expect(await screen.findByText("Role saved")).toBeInTheDocument();
    const row = await rowOf("cab_coffee");
    await waitFor(() => expect(spoken(within(row).getAllByRole("cell")[2]!)).toBe("Barista"));
  });

  test("a refusal shows the API's message in the dialog, which stays", async () => {
    const { user } = show({
      [ROLE]: apiError(
        403,
        "FORBIDDEN",
        "This account is an admin by the server's configuration. Its role cannot be changed here.",
      ),
    });
    const dialog = await openStep(user, "Change role");

    await user.selectOptions(within(dialog).getByLabelText("Role"), "Admin");
    await user.click(within(dialog).getByRole("button", { name: "Save role" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "This account is an admin by the server's configuration. Its role cannot be changed here.",
    );
    expect(screen.getByRole("dialog", { name: "cab_coffee" })).toBeInTheDocument();
  });

  test("two submits in one moment are one request: the second is stopped where it is made", async () => {
    const answer = deferred();
    const { api, user } = show({ [ROLE]: answer.handler });
    const dialog = await openStep(user, "Change role");
    await user.selectOptions(within(dialog).getByLabelText("Role"), "Barista");

    submitTwiceAtOnce(dialog);

    await waitFor(() => expect(api.calls(ROLE)).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(api.calls(ROLE)).toHaveLength(1);
    expect(within(dialog).getByRole("button", { name: "Close" })).toBeDisabled();
  });
});

describe("Adjust tokens", () => {
  const REASON = "Helped at the welcome desk.";

  /** Opens the first step and fills it in. */
  async function ask(user: User, amount: string, direction: "Add" | "Remove" = "Add") {
    const dialog = await openStep(user, "Adjust tokens");
    if (direction === "Remove") {
      await user.click(within(dialog).getByRole("radio", { name: "Remove" }));
    }
    await user.type(within(dialog).getByLabelText("Amount"), amount);
    await user.type(within(dialog).getByLabelText("Reason"), REASON);
    return dialog;
  }

  /** The same, on to the second step. */
  async function review(user: User, amount: string, direction: "Add" | "Remove" = "Add") {
    const dialog = await ask(user, amount, direction);
    await user.click(within(dialog).getByRole("button", { name: "Review" }));
    return dialog;
  }

  test("the first step asks for Add or Remove, an amount and a reason, and Review waits for both", async () => {
    const { user } = show();
    const dialog = await openStep(user, "Adjust tokens");

    expect(within(dialog).getByRole("heading", { level: 3, name: "Adjust tokens" })).toHaveFocus();
    const control = within(dialog).getByRole("radiogroup", { name: "Add or remove tokens" });
    expect(within(control).getByRole("radio", { name: "Add" })).toBeChecked();
    expect(within(control).getByRole("radio", { name: "Remove" })).not.toBeChecked();
    const amount = within(dialog).getByLabelText("Amount");
    const review = within(dialog).getByRole("button", { name: "Review" });
    expect(review).toBeDisabled();

    // Digits and one decimal point: a comma becomes a point, nothing else is taken.
    await user.type(amount, "-1x2,5.0");
    expect(amount).toHaveValue("12.50");
    expect(amount).toHaveAttribute("inputmode", "decimal");
    expect(review).toBeDisabled();

    await user.type(within(dialog).getByLabelText("Reason"), REASON);
    expect(within(dialog).getByLabelText("Reason")).toHaveAccessibleDescription(
      "Kept in the audit log.",
    );
    expect(review).toBeEnabled();
  });

  test("Review restates the adjustment in one sentence before anything is sent, and Back keeps what was typed", async () => {
    const { api, user } = show();
    const dialog = await review(user, "1250.5");

    expect(within(dialog).getByText("Add 1,250.5 tokens to cab_coffee?")).toBeInTheDocument();
    expect(within(dialog).getByText(REASON)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Add tokens" })).toHaveAttribute(
      "data-variant",
      "primary",
    );
    expect(api.calls(ADJUST)).toHaveLength(0);

    await user.click(within(dialog).getByRole("button", { name: "Back" }));

    expect(within(dialog).getByLabelText("Amount")).toHaveValue("1250.5");
    expect(within(dialog).getByLabelText("Reason")).toHaveValue(REASON);
    expect(api.calls(ADJUST)).toHaveLength(0);
  });

  test("the sentence names the direction and the amount as it was typed", async () => {
    const { user } = show();
    const dialog = await review(user, "1", "Remove");

    expect(within(dialog).getByText("Remove 1 token from cab_coffee?")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Remove tokens" })).toBeInTheDocument();
  });

  test("the confirming tap sends the amount as it was typed, the reason and an Idempotency-Key; the answer's balance is what the row shows", async () => {
    const { api, user } = show({ [ADJUST]: json(201, balanceAdjusted({ balance_cr: "1015" })) });
    const dialog = await review(user, "35.00");

    await user.click(within(dialog).getByRole("button", { name: "Add tokens" }));

    await gone();
    expect(api.lastCall(ADJUST)?.path).toBe(`/api/admin/users/${OTHER_USER_ID}/adjustments`);
    // Never a number made from the amount: "35.00" would have become 35.
    expect(api.lastCall(ADJUST)?.rawBody).toBe(
      '{"amount_cr":"35.00","reason":"Helped at the welcome desk."}',
    );
    expect(api.lastCall(ADJUST)?.headers.get("Idempotency-Key")).toMatch(UUID);
    expect(await screen.findByText("Tokens adjusted")).toBeInTheDocument();
    const row = await rowOf("cab_coffee");
    await waitFor(() => expect(spoken(within(row).getAllByRole("cell")[4]!)).toBe("1,015 tokens"));
    // The balance came with the answer: the list was not asked again for it.
    expect(api.calls(USERS)).toHaveLength(1);
  });

  test("Remove puts a minus sign in front of what was typed, and changes nothing else of it", async () => {
    const { api, user } = show({
      [ADJUST]: json(201, balanceAdjusted({ amount_cr: "-12.5", balance_cr: "967.5" })),
    });
    const dialog = await review(user, "012,50", "Remove");

    await user.click(within(dialog).getByRole("button", { name: "Remove tokens" }));

    await gone();
    expect(api.lastCall(ADJUST)?.rawBody).toBe(
      '{"amount_cr":"-012.50","reason":"Helped at the welcome desk."}',
    );
  });

  test.each([
    ["INSUFFICIENT_BALANCE", 409, "This account holds fewer tokens than that."],
    ["LIMIT_EXCEEDED", 409, "One adjustment can add at most 1,000 tokens."],
    ["FORBIDDEN", 403, "You cannot do this to your own account. Ask another admin."],
  ] as const)(
    "%s shows the API's sentence above the button, and the dialog stays",
    async (code, status, message) => {
      const { api, user } = show({ [ADJUST]: apiError(status, code, message) });
      const dialog = await review(user, "35000");

      await user.click(within(dialog).getByRole("button", { name: "Add tokens" }));

      expect(await within(dialog).findByRole("alert")).toHaveTextContent(message);
      expect(screen.getByRole("dialog", { name: "cab_coffee" })).toBeInTheDocument();
      // The adjustment still stands as it was restated, and can be sent again or changed.
      expect(within(dialog).getByText("Add 35,000 tokens to cab_coffee?")).toBeInTheDocument();
      expect(within(dialog).getByRole("button", { name: "Add tokens" })).toBeEnabled();
      expect(within(dialog).getByRole("button", { name: "Back" })).toBeEnabled();
      expect(api.calls(ADJUST)).toHaveLength(1);

      // Going back to change it takes the refusal with it.
      await user.click(within(dialog).getByRole("button", { name: "Back" }));
      expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
      expect(within(dialog).getByLabelText("Amount")).toHaveValue("35000");
    },
  );

  test("a refusal that names the amount brings the first step back, with the API's sentence on the field", async () => {
    const { user } = show({
      [ADJUST]: apiError(422, "VALIDATION_ERROR", "The request is not valid.", {
        errors: [
          { loc: ["body", "amount_cr"], msg: "An amount cannot be zero.", type: "decimal_zero" },
        ],
      }),
    });
    const dialog = await review(user, "0");

    await user.click(within(dialog).getByRole("button", { name: "Add tokens" }));

    const amount = await within(dialog).findByLabelText("Amount");
    await waitFor(() => expect(amount).toBeInvalid());
    expect(amount).toHaveAccessibleDescription("An amount cannot be zero.");
    expect(amount).toHaveValue("0");
    expect(within(dialog).getByLabelText("Reason")).toHaveValue(REASON);

    // The refusal is about what was sent: it goes when the amount changes.
    await user.type(amount, "5");
    expect(amount).not.toBeInvalid();
  });

  test("a second attempt after a refusal is another request with another key", async () => {
    const { api, user } = show({
      [ADJUST]: apiError(409, "INSUFFICIENT_BALANCE", "This account holds fewer tokens than that."),
    });
    const dialog = await review(user, "5000", "Remove");

    await user.click(within(dialog).getByRole("button", { name: "Remove tokens" }));
    await within(dialog).findByRole("alert");
    api.on(ADJUST, json(201, balanceAdjusted({ amount_cr: "-5000", balance_cr: "0" })));
    await user.click(within(dialog).getByRole("button", { name: "Remove tokens" }));

    await gone();
    const keys = api.calls(ADJUST).map((request) => request.headers.get("Idempotency-Key"));
    expect(keys).toHaveLength(2);
    expect(keys[0]).not.toBe(keys[1]);
  });

  test("IDEMPOTENCY_KEY_REUSED shows our own sentence in place of the message about a header", async () => {
    const { user } = show({
      [ADJUST]: apiError(
        422,
        "IDEMPOTENCY_KEY_REUSED",
        "This Idempotency-Key was used for another request.",
      ),
    });
    const dialog = await review(user, "35");

    await user.click(within(dialog).getByRole("button", { name: "Add tokens" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Something went wrong. Try again.",
    );
    expect(within(dialog).queryByText(/Idempotency-Key/)).not.toBeInTheDocument();
  });

  test("from the confirming tap until the answer the dialog cannot be closed, and two taps are one adjustment", async () => {
    const answer = deferred();
    const { api, user } = show({ [ADJUST]: answer.handler });
    const dialog = await review(user, "35");

    await user.click(within(dialog).getByRole("button", { name: "Add tokens" }));
    await user.keyboard("{Enter}");
    await user.keyboard("{Escape}");

    const go = within(dialog).getByRole("button", { name: "Add tokens" });
    expect(go).toHaveAttribute("aria-busy", "true");
    expect(within(dialog).getByRole("button", { name: "Close" })).toBeDisabled();
    // Nothing but the adjustment as it was sent: no Back, no field.
    expect(within(dialog).queryByRole("button", { name: "Back" })).not.toBeInTheDocument();
    expect(within(dialog).getByText("Add 35 tokens to cab_coffee?")).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "cab_coffee" })).toBeInTheDocument();
    expect(api.calls(ADJUST)).toHaveLength(1);

    answer.resolve(json(201, balanceAdjusted()));
    await gone();
    expect(api.calls(ADJUST)).toHaveLength(1);
  });

  test("two confirming submits in one moment are one adjustment: the second is stopped where it is made", async () => {
    const answer = deferred();
    const { api, user } = show({ [ADJUST]: answer.handler });
    const dialog = await review(user, "35");

    submitTwiceAtOnce(dialog);

    await waitFor(() => expect(api.calls(ADJUST)).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(api.calls(ADJUST)).toHaveLength(1);
  });

  test.each([
    ["no answer at all", noAnswer, "No connection. Try again."],
    [
      "an error of the server",
      apiError(503, "SERVICE_UNAVAILABLE", "This is temporarily unavailable. Try again shortly."),
      "This is temporarily unavailable. Try again shortly.",
    ],
  ] as const)(
    "after %s the adjustment is kept, and Try again sends the same body with the same key",
    async (_name, failure, message) => {
      const { api, user } = show({ [ADJUST]: failure });
      const dialog = await review(user, "35.00");

      await user.click(within(dialog).getByRole("button", { name: "Add tokens" }));

      // The adjustment as it was sent, the failure, and nothing but "Try again".
      const again = await within(dialog).findByRole("button", { name: "Try again" });
      expect(within(dialog).getByRole("alert")).toHaveTextContent(message);
      expect(within(dialog).getByText("Add 35.00 tokens to cab_coffee?")).toBeInTheDocument();
      expect(within(dialog).getByText(REASON)).toBeInTheDocument();
      expect(within(dialog).queryByRole("button", { name: "Back" })).not.toBeInTheDocument();
      expect(within(dialog).queryByLabelText("Amount")).not.toBeInTheDocument();
      // The list was not read to guess at it: the request itself is sent again.
      expect(api.calls(USERS)).toHaveLength(1);

      api.on(ADJUST, json(201, balanceAdjusted()));
      await user.click(again);

      await gone();
      const [first, second] = api.calls(ADJUST);
      expect(api.calls(ADJUST)).toHaveLength(2);
      expect(second!.rawBody).toBe(first!.rawBody);
      expect(second!.rawBody).toBe('{"amount_cr":"35.00","reason":"Helped at the welcome desk."}');
      expect(second!.headers.get("Idempotency-Key")).toBe(first!.headers.get("Idempotency-Key"));
      expect(await screen.findByText("Tokens adjusted")).toBeInTheDocument();
    },
  );

  test("an adjustment without an answer is still there when the dialog was closed and opened again, and nothing is sent without a tap", async () => {
    const { api, user } = show({ [ADJUST]: noAnswer });
    const dialog = await review(user, "35", "Remove");
    await user.click(within(dialog).getByRole("button", { name: "Remove tokens" }));
    await within(dialog).findByRole("button", { name: "Try again" });

    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    await gone();
    const again = await openUser(user);

    // Not the facts and their buttons: the adjustment that waits is all the dialog shows.
    expect(within(again).getByText("Remove 35 tokens from cab_coffee?")).toBeInTheDocument();
    expect(within(again).getByText(REASON)).toBeInTheDocument();
    expect(again.querySelector("[data-slot=user-actions]")).not.toBeInTheDocument();
    expect(within(again).getByRole("button", { name: "Try again" })).toBeEnabled();
    expect(api.calls(ADJUST)).toHaveLength(1);

    api.on(ADJUST, json(201, balanceAdjusted({ amount_cr: "-35", balance_cr: "945" })));
    await user.click(within(again).getByRole("button", { name: "Try again" }));

    await gone();
    const [first, second] = api.calls(ADJUST);
    expect(second!.rawBody).toBe(first!.rawBody);
    expect(second!.headers.get("Idempotency-Key")).toBe(first!.headers.get("Idempotency-Key"));
    const row = await rowOf("cab_coffee");
    await waitFor(() => expect(spoken(within(row).getAllByRole("cell")[4]!)).toBe("945 tokens"));
  });

  test("the adjustment of one user is kept apart from the dialog of another", async () => {
    const other = adminUser({ id: "another", username: "polybahn", email: "p@ethz.ch" });
    const { user } = show({
      [USERS]: json(200, adminUsers({ items: [adminUser(), other, adminUsers().items[1]!] })),
      [ADJUST]: noAnswer,
    });
    const dialog = await review(user, "35");
    await user.click(within(dialog).getByRole("button", { name: "Add tokens" }));
    await within(dialog).findByRole("button", { name: "Try again" });
    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    await gone();

    const another = await openUser(user, "polybahn");

    expect(actions(another)).toEqual(["Suspend", "Change role", "Adjust tokens"]);
    expect(within(another).queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });

  test("RATE_LIMITED keeps the adjustment too, and Try again waits for the time the API names", async () => {
    const { api, user } = show({
      [ADJUST]: apiError(429, "RATE_LIMITED", "Too many requests. Try again shortly.", {
        retry_after_seconds: 30,
      }),
    });
    const dialog = await review(user, "35");

    await user.click(within(dialog).getByRole("button", { name: "Add tokens" }));

    const again = await within(dialog).findByRole("button", { name: "Try again" });
    expect(again).toBeDisabled();
    expect(within(dialog).getByRole("alert")).toHaveTextContent(
      "Too many requests. Try again shortly.",
    );
    expect(dialog.querySelector("[data-slot=retry-countdown]")).toHaveTextContent(
      /Try again in \d+ s/,
    );
    expect(api.calls(ADJUST)).toHaveLength(1);
  });

  test("an adjustment that is booked while no dialog shows its user is said with a toast", async () => {
    const answer = deferred();
    const client = createTestQueryClient();
    const api = mockApi({ [USERS]: json(200, adminUsers()), [ADJUST]: answer.handler });
    const { user, unmount } = renderWithProviders(<AdminUsersScreen />, { ...admin, client });
    const dialog = await review(user, "35");
    await user.click(within(dialog).getByRole("button", { name: "Add tokens" }));
    await waitFor(() => expect(api.calls(ADJUST)).toHaveLength(1));

    // The admin left the screen while the request was out.
    unmount();
    renderWithProviders(<p>Another screen</p>, { ...admin, client });
    answer.resolve(json(201, balanceAdjusted()));

    expect(await screen.findByText("Tokens adjusted")).toBeInTheDocument();
  });
});
