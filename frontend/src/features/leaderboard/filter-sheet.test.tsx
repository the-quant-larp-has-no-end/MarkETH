import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { TIMES } from "@/lib/format";
import { leaderboard } from "@/test/fixtures";
import {
  apiError,
  deferred,
  type Handler,
  json,
  type MockRequest,
  mockApi,
  noAnswer,
  type Route,
} from "@/test/http";
import { setViewport } from "@/test/media";
import { followHistoryWrites, navigation } from "@/test/navigation";
import { renderWithProviders } from "@/test/render";

import { LeaderboardScreen } from "./leaderboard-screen";
import { player } from "./test-players";

/**
 * The filter sheet, as the Leaderboard uses it: the address, the request and the list
 * behind the sheet are part of what it does, so the tests go through the screen.
 */

const BOARD: Route = "GET /api/leaderboard";

type User = ReturnType<typeof renderWithProviders>["user"];

const ANSWER = json(200, leaderboard());
/** What the filtered list answers: one player nobody else is. */
const FILTERED = json(200, leaderboard({ items: [player(1, "only_one")], viewer: null }));

const sent = (request: MockRequest | undefined) => Object.fromEntries(request?.query ?? []);
const isFiltered = (request: MockRequest) =>
  [...request.query.keys()].some((name) => name !== "sort" && name !== "order");

/** Answers the plain list with the four players, and anything filtered with `filtered`. */
function boardApi(filtered: Handler = FILTERED) {
  return mockApi({
    [BOARD]: (request: MockRequest) =>
      isFiltered(request)
        ? typeof filtered === "function"
          ? filtered(request)
          : filtered
        : ANSWER,
  });
}

/** A request that does not match its schema: one sentence of the API per field. */
function invalid(...errors: { loc: string[]; msg: string }[]) {
  return apiError(422, "VALIDATION_ERROR", "The request is not valid.", {
    errors: errors.map((error) => ({ ...error, type: "x" })),
  });
}

/** A rule the service checks: it names its field, and the error's message is its sentence. */
function refused(field: string, message: string) {
  return apiError(422, "VALIDATION_ERROR", message, { field });
}

const NOT_A_PERCENTAGE = "Enter a percentage between 0 and 100.";
const BOUNDS_OUT_OF_ORDER = "Enter a lower bound that is not above the upper one.";

const sheet = () => screen.getByRole("dialog", { name: "Filter" });
const level = () => within(sheet()).getByLabelText("Level");
const programme = () => within(sheet()).getByLabelText("Programme");
const row = (measure: string) =>
  within(sheet()).getByRole("group", { name: new RegExp(`^${measure}`) });
const from = (measure: string) => within(row(measure)).getByLabelText("From");
const to = (measure: string) => within(row(measure)).getByLabelText("To");
const show = () => within(sheet()).getByRole("button", { name: "Show results" });
const clearAll = () => within(sheet()).getByRole("button", { name: "Clear all" });

function options(select: HTMLElement): string[] {
  return Array.from(select.querySelectorAll("option")).map((option) => option.textContent ?? "");
}

async function openSheet(user: User) {
  await user.click(screen.getByRole("button", { name: /^Filter/ }));
  return screen.findByRole("dialog", { name: "Filter" });
}

/** The bottom sheet takes half a second to slide away before it is gone. */
async function closed() {
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument(), {
    timeout: 3000,
  });
}

async function renderOpen(address = "/leaderboard") {
  navigation.go(address);
  const rendered = renderWithProviders(<LeaderboardScreen />);
  await waitFor(() => expect(document.querySelector("[data-slot=ranking]")).toBeInTheDocument());
  await openSheet(rendered.user);
  return rendered;
}

beforeEach(() => {
  navigation.go("/leaderboard");
  followHistoryWrites();
});

describe("what the sheet offers", () => {
  test("a Level and a Programme select from /config, then one group per measure with a From and a To field", async () => {
    boardApi();
    await renderOpen();

    expect(within(sheet()).getByRole("heading", { name: "Filter" })).toBeInTheDocument();
    expect(within(sheet()).getByRole("button", { name: "Close" })).toBeEnabled();
    expect(options(level())).toEqual(["Any level", "Bachelor", "Master", "Doctorate", "Other"]);
    expect(level()).toHaveValue("");
    expect(programme()).toHaveValue("");

    const groups = within(sheet()).getAllByRole("group");
    expect(
      groups.map((group) => group.querySelector(".type-label")?.firstChild?.textContent),
    ).toEqual(["Tokens", "Win rate", "Trades", "Payout"]);
    for (const group of groups) {
      expect(within(group).getByLabelText("From")).toHaveValue("");
      expect(within(group).getByLabelText("To")).toHaveValue("");
    }
  });

  test("the two fields of a group stand side by side in equal columns, aligned at their tops", async () => {
    boardApi();
    await renderOpen();

    for (const measure of ["Tokens", "Win rate", "Trades", "Payout"]) {
      const columns = from(measure).closest(".grid") as HTMLElement;
      expect(columns).toHaveClass("grid-cols-2", "gap-3", "items-start");
      expect(columns.contains(to(measure))).toBe(true);
    }
    // The selects are as wide as the sheet: they stand outside any pair of columns.
    expect(level().closest(".grid-cols-2")).toBeNull();
    expect(programme().closest(".grid-cols-2")).toBeNull();
  });

  test("the unit a bound is typed in stands inside its field and is said with its group", async () => {
    boardApi();
    await renderOpen();

    expect(row("Tokens")).toHaveAccessibleName("Tokens");
    expect(row("Win rate")).toHaveAccessibleName("Win rate, in percent");
    expect(row("Win rate")).toHaveTextContent("%");
    expect(row("Trades")).toHaveAccessibleName("Trades");
    expect(row("Payout")).toHaveAccessibleName("Payout, tokens returned per token put in");
    expect(row("Payout")).toHaveTextContent(TIMES);
    // The text of such a field ends 36 px before its right end, where the unit stands.
    expect(from("Win rate")).toHaveClass("pr-9");
    expect(to("Payout")).toHaveClass("pr-9");
    expect(from("Tokens")).not.toHaveClass("pr-9");
  });

  test("the Tokens group says what the figure is made of", async () => {
    boardApi();
    await renderOpen();

    expect(row("Tokens")).toHaveAccessibleDescription("Wallet plus open positions");
    expect(within(row("Tokens")).getByText("Wallet plus open positions")).toHaveClass(
      "type-caption",
      "text-muted-foreground",
    );
    expect(row("Trades")).not.toHaveAccessibleDescription();
  });

  test("a phone gets the keypad of the figure: decimal, or digits for a whole number", async () => {
    boardApi();
    await renderOpen();

    expect(from("Tokens")).toHaveAttribute("inputmode", "decimal");
    expect(from("Win rate")).toHaveAttribute("inputmode", "decimal");
    expect(from("Trades")).toHaveAttribute("inputmode", "numeric");
    expect(to("Payout")).toHaveAttribute("inputmode", "decimal");
    expect(from("Tokens")).toHaveAttribute("name", "min_credits_cr");
    expect(to("Payout")).toHaveAttribute("name", "max_avg_payout_ratio");
  });

  test("the Programme select is disabled until a level is chosen, then holds the programmes of that level", async () => {
    boardApi();
    const { user } = await renderOpen();

    expect(programme()).toBeDisabled();

    await user.selectOptions(level(), "Master");

    expect(programme()).toBeEnabled();
    expect(options(programme())).toEqual([
      "Any programme",
      "MSc Computer Science",
      "MSc Data Science",
    ]);
  });

  test("changing the level clears the programme; a level can be set back to any", async () => {
    boardApi();
    const { user } = await renderOpen();
    await user.selectOptions(level(), "Master");
    await user.selectOptions(programme(), "MSc Data Science");

    await user.selectOptions(level(), "Bachelor");
    expect(programme()).toHaveValue("");
    expect(options(programme())).toContain("BSc Physics");

    await user.selectOptions(level(), "Any level");
    expect(level()).toHaveValue("");
    expect(programme()).toBeDisabled();
  });

  test("one primary button, Show results, and a ghost Clear all with nothing to clear", async () => {
    boardApi();
    await renderOpen();

    expect(show()).toHaveAttribute("data-variant", "primary");
    expect(show()).toHaveAttribute("type", "submit");
    expect(document.querySelectorAll("[data-slot=button][data-variant=primary]")).toHaveLength(1);
    expect(clearAll()).toHaveAttribute("data-variant", "ghost");
    expect(clearAll()).toBeDisabled();
  });

  test("a field keeps digits and one decimal point, a comma becomes the point, and a whole number keeps digits alone", async () => {
    boardApi();
    const { user } = await renderOpen();

    await user.type(from("Win rate"), "62,5%");
    expect(from("Win rate")).toHaveValue("62.5");

    await user.type(from("Tokens"), "12a.5.0 tokens");
    expect(from("Tokens")).toHaveValue("12.50");

    await user.type(to("Payout"), "x1,25");
    expect(to("Payout")).toHaveValue("1.25");

    await user.type(from("Trades"), "1,5x");
    expect(from("Trades")).toHaveValue("15");
  });

  test("from 768 px the sheet is a dialog with the same fields", async () => {
    setViewport("wide");
    boardApi();
    await renderOpen();

    expect(sheet()).toHaveAttribute("data-slot", "dialog-content");
    expect(level()).toBeInTheDocument();
    expect(from("Payout")).toBeInTheDocument();
  });
});

describe("Show results", () => {
  test("sends every filter under the API's name, puts them in the address and closes the sheet", async () => {
    const api = boardApi();
    const { user } = await renderOpen();

    await user.selectOptions(level(), "Master");
    await user.selectOptions(programme(), "MSc Data Science");
    await user.type(from("Tokens"), "0100.50");
    await user.type(to("Tokens"), "12345678901234567890.123456");
    await user.type(from("Win rate"), "62.5");
    await user.type(to("Win rate"), "100");
    await user.type(from("Trades"), "007");
    await user.type(to("Payout"), "1.50");
    await user.click(show());

    await closed();
    expect(await screen.findByText("only_one")).toBeInTheDocument();
    const expected = {
      sort: "credits",
      order: "desc",
      degree_level: "MASTER",
      degree_programme: "MSc Data Science",
      // The strings that were typed: an amount is not read as a number on its way out.
      min_credits_cr: "0100.50",
      max_credits_cr: "12345678901234567890.123456",
      // A percentage, sent as a fraction by moving the point in the string.
      min_win_rate: "0.625",
      max_win_rate: "1",
      min_trades: "007",
      max_avg_payout_ratio: "1.50",
    };
    expect(sent(api.lastCall(BOARD))).toEqual(expected);
    expect(Object.fromEntries(new URLSearchParams(navigation.search))).toEqual({
      degree_level: "MASTER",
      degree_programme: "MSc Data Science",
      min_credits_cr: "0100.50",
      max_credits_cr: "12345678901234567890.123456",
      min_win_rate: "0.625",
      max_win_rate: "1",
      min_trades: "007",
      max_avg_payout_ratio: "1.50",
    });
    expect(navigation.pathname).toBe("/leaderboard");
  });

  test("the list is asked once: what the sheet read is what the list shows", async () => {
    const api = boardApi();
    const { user } = await renderOpen();

    await user.type(from("Trades"), "3");
    await user.click(show());

    await closed();
    await screen.findByText("only_one");
    expect(api.calls(BOARD).map(sent)).toEqual([
      { sort: "credits", order: "desc" },
      { sort: "credits", order: "desc", min_trades: "3" },
    ]);
  });

  test("the filters then stand under the sort chips as chips, and Filter counts them", async () => {
    boardApi();
    const { user } = await renderOpen();

    await user.selectOptions(level(), "Bachelor");
    await user.selectOptions(programme(), "BSc Computer Science");
    await user.type(from("Win rate"), "50");
    await user.click(show());

    await closed();
    const chips = within(
      await screen.findByRole("list", { name: "Filters that are set" }),
    ).getAllByRole("button");
    expect(chips.map((button) => button.textContent)).toEqual([
      "BSc Computer Science",
      "Win rate from 50%",
    ]);
    expect(screen.getByRole("button", { name: "Filter, 2 set" })).toBeInTheDocument();
  });

  test("keeps the sort and the order of the list", async () => {
    const api = boardApi();
    const { user } = await renderOpen("/leaderboard?sort=win_rate&order=asc");

    await user.type(to("Trades"), "40");
    await user.click(show());

    await closed();
    expect(sent(api.lastCall(BOARD))).toEqual({ sort: "win_rate", order: "asc", max_trades: "40" });
    expect(navigation.href).toBe("/leaderboard?sort=win_rate&order=asc&max_trades=40");
  });

  test("a from above its to is not compared here: both are sent, and the API is the judge", async () => {
    const api = boardApi();
    const { user } = await renderOpen();

    await user.type(from("Tokens"), "500");
    await user.type(to("Tokens"), "100");
    await user.click(show());

    await closed();
    expect(sent(api.lastCall(BOARD))).toMatchObject({
      min_credits_cr: "500",
      max_credits_cr: "100",
    });
  });

  test("what a field shows is what is sent: the characters as typed, a percentage with its point moved", async () => {
    const answer = deferred();
    const api = boardApi(answer.handler);
    const { user } = await renderOpen();

    // A decimal comma, as a phone's keypad types it; a point nothing follows yet.
    await user.type(from("Tokens"), "1204,5");
    await user.type(from("Win rate"), "62,5");
    await user.type(to("Win rate"), "80.");
    await user.type(to("Payout"), "1,50");
    await user.click(show());

    await waitFor(() =>
      expect(sent(api.lastCall(BOARD))).toEqual({
        sort: "credits",
        order: "desc",
        min_credits_cr: "1204.5",
        min_win_rate: "0.625",
        max_win_rate: "0.8",
        max_avg_payout_ratio: "1.50",
      }),
    );
    // Sending changes no field.
    expect(from("Tokens")).toHaveValue("1204.5");
    expect(from("Win rate")).toHaveValue("62.5");
    expect(to("Win rate")).toHaveValue("80.");
    expect(to("Payout")).toHaveValue("1.50");
    answer.resolve(FILTERED);
    await closed();
  });

  test("while the API has not answered the button waits, the list keeps its rows, and a second submit sends nothing", async () => {
    const answer = deferred();
    const api = boardApi(answer.handler);
    const { user } = await renderOpen();

    await user.type(from("Trades"), "3");
    await user.click(show());
    await user.click(show());
    await user.keyboard("{Enter}");

    expect(show()).toBeDisabled();
    expect(show()).toHaveAttribute("aria-busy", "true");
    expect(api.calls(BOARD)).toHaveLength(2);
    expect(navigation.href).toBe("/leaderboard");
    expect(screen.getByText("polyterrasse")).toBeInTheDocument();

    answer.resolve(FILTERED);

    await closed();
    expect(await screen.findByText("only_one")).toBeInTheDocument();
    expect(api.calls(BOARD)).toHaveLength(2);
    expect(navigation.href).toBe("/leaderboard?min_trades=3");
  });

  test("with nothing changed it closes the sheet and leaves the address alone", async () => {
    boardApi();
    const { user } = await renderOpen("/leaderboard?min_trades=3");

    await user.click(show());

    await closed();
    expect(navigation.router.push).not.toHaveBeenCalled();
    expect(navigation.href).toBe("/leaderboard?min_trades=3");
  });

  test("the back button returns to the list as it was before the filters", async () => {
    boardApi();
    const { user } = await renderOpen();

    await user.type(from("Trades"), "3");
    await user.click(show());
    await closed();
    await screen.findByText("only_one");

    act(() => navigation.router.back());

    expect(navigation.href).toBe("/leaderboard");
    expect(await screen.findByText("polyterrasse")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Filters that are set" })).not.toBeInTheDocument();
  });
});

describe("a value the API rejects", () => {
  test("VALIDATION_ERROR is marked on the field it names with the API's sentence; the list keeps its last result and the address stays", async () => {
    boardApi(invalid({ loc: ["query", "max_win_rate"], msg: NOT_A_PERCENTAGE }));
    const intoView = vi.spyOn(Element.prototype, "scrollIntoView");
    const { user } = await renderOpen();

    await user.type(from("Win rate"), "50");
    await user.type(to("Win rate"), "150");
    await user.click(show());

    await waitFor(() => expect(to("Win rate")).toBeInvalid());
    expect(to("Win rate")).toHaveAccessibleDescription(NOT_A_PERCENTAGE);
    // The fields scroll under the buttons: the refused one is brought into view and
    // takes the focus, so that its message is seen and read out.
    await waitFor(() => expect(to("Win rate")).toHaveFocus());
    expect(intoView.mock.contexts).toContain(to("Win rate"));
    const alert = within(sheet()).getByRole("alert");
    expect(alert).toHaveTextContent(NOT_A_PERCENTAGE);
    // The sentence stands under its field, inside the field's column.
    expect(to("Win rate").closest("[data-slot=field]")!.contains(alert)).toBe(true);
    expect(from("Win rate")).not.toBeInvalid();
    // Nothing of ours is said, and nothing is applied.
    expect(within(sheet()).getAllByRole("alert")).toHaveLength(1);
    expect(sheet()).toBeInTheDocument();
    expect(navigation.href).toBe("/leaderboard");
    expect(navigation.router.push).not.toHaveBeenCalled();
    expect(screen.getByText("polyterrasse")).toBeInTheDocument();
    expect(screen.getByText("mensa_fan")).toBeInTheDocument();
    // The input is kept, and the button is ready for another try.
    expect(from("Win rate")).toHaveValue("50");
    expect(to("Win rate")).toHaveValue("150");
    expect(show()).toBeEnabled();
  });

  test("a lower bound above its upper one: the API names the lower field, and its sentence stands under that field", async () => {
    const api = boardApi(refused("min_credits_cr", BOUNDS_OUT_OF_ORDER));
    const { user } = await renderOpen();

    await user.type(from("Tokens"), "500");
    await user.type(to("Tokens"), "100");
    await user.click(show());

    // Both went out as typed: the sheet compared nothing.
    expect(sent(api.lastCall(BOARD))).toMatchObject({
      min_credits_cr: "500",
      max_credits_cr: "100",
    });
    await waitFor(() => expect(from("Tokens")).toBeInvalid());
    expect(from("Tokens")).toHaveAccessibleDescription(BOUNDS_OUT_OF_ORDER);
    expect(to("Tokens")).not.toBeInvalid();
    // Once, on the field, and not a second time above the button.
    expect(within(sheet()).getAllByRole("alert")).toHaveLength(1);
    expect(within(sheet()).queryByText("The request is not valid.")).not.toBeInTheDocument();
    expect(navigation.href).toBe("/leaderboard");
    expect(screen.getByText("polyterrasse")).toBeInTheDocument();
  });

  test.each([
    ["Win rate", "min_win_rate"],
    ["Trades", "min_trades"],
    ["Payout", "min_avg_payout_ratio"],
  ])("a crossed pair of %s is marked on its From field", async (measure, field) => {
    boardApi(refused(field, BOUNDS_OUT_OF_ORDER));
    const { user } = await renderOpen();

    await user.type(from(measure), "9");
    await user.type(to(measure), "1");
    await user.click(show());

    await waitFor(() => expect(from(measure)).toBeInvalid());
    expect(from(measure)).toHaveAccessibleDescription(BOUNDS_OUT_OF_ORDER);
    expect(to(measure)).not.toBeInvalid();
  });

  test("each field the API names is marked, whichever it is", async () => {
    boardApi(
      invalid(
        { loc: ["query", "min_credits_cr"], msg: "Use at most 6 decimal places." },
        { loc: ["query", "degree_programme"], msg: "Use at most 100 characters." },
        { loc: ["query", "min_trades"], msg: "Enter a whole number." },
      ),
    );
    const { user } = await renderOpen();

    await user.selectOptions(level(), "Master");
    await user.selectOptions(programme(), "MSc Data Science");
    await user.type(from("Tokens"), "0.1234567");
    await user.type(from("Trades"), "3");
    await user.click(show());

    await waitFor(() => expect(from("Tokens")).toBeInvalid());
    expect(from("Tokens")).toHaveAccessibleDescription("Use at most 6 decimal places.");
    expect(programme()).toBeInvalid();
    expect(programme()).toHaveAccessibleDescription("Use at most 100 characters.");
    expect(from("Trades")).toBeInvalid();
    expect(from("Trades")).toHaveAccessibleDescription("Enter a whole number.");
    expect(level()).not.toBeInvalid();
    expect(to("Tokens")).not.toBeInvalid();
  });

  test("an error that names no field stands above the button, with the API's message", async () => {
    boardApi(invalid({ loc: ["query"], msg: "This value is not valid." }));
    const { user } = await renderOpen();

    await user.type(from("Trades"), "11");
    await user.type(to("Trades"), "10");
    await user.click(show());

    const alert = await within(sheet()).findByRole("alert");
    expect(alert).toHaveTextContent("The request is not valid.");
    expect(alert.compareDocumentPosition(show())).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    expect(from("Trades")).not.toBeInvalid();
    expect(to("Trades")).not.toBeInvalid();
    expect(navigation.href).toBe("/leaderboard");
  });

  test("an error goes away once the input it was about changes, and the corrected filter is applied", async () => {
    const api = boardApi((request) =>
      request.query.get("max_win_rate") === "1.5"
        ? invalid({ loc: ["query", "max_win_rate"], msg: NOT_A_PERCENTAGE })
        : FILTERED,
    );
    const { user } = await renderOpen();
    await user.type(to("Win rate"), "150");
    await user.click(show());
    await waitFor(() => expect(to("Win rate")).toBeInvalid());

    await user.type(to("Win rate"), "{Backspace}");

    expect(to("Win rate")).not.toBeInvalid();
    expect(within(sheet()).queryByRole("alert")).not.toBeInTheDocument();

    await user.click(show());

    await closed();
    expect(sent(api.lastCall(BOARD))).toMatchObject({ max_win_rate: "0.15" });
    expect(navigation.href).toBe("/leaderboard?max_win_rate=0.15");
  });

  test("an address that holds a rejected filter: the banner on the screen, and the field marked when the sheet opens", async () => {
    navigation.go("/leaderboard?max_win_rate=66.7&min_trades=3");
    mockApi({ [BOARD]: invalid({ loc: ["query", "max_win_rate"], msg: NOT_A_PERCENTAGE }) });
    const { user } = renderWithProviders(<LeaderboardScreen />);

    expect(await screen.findByRole("alert")).toHaveTextContent("The request is not valid.");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
    // The filters can still be seen and removed.
    expect(
      screen.getByRole("button", { name: "Remove filter: Win rate to 6670%" }),
    ).toBeInTheDocument();

    const intoView = vi.spyOn(Element.prototype, "scrollIntoView");
    await openSheet(user);

    expect(to("Win rate")).toBeInvalid();
    expect(to("Win rate")).toHaveValue("6670");
    expect(to("Win rate")).toHaveAccessibleDescription(NOT_A_PERCENTAGE);
    expect(from("Trades")).toHaveValue("3");
    expect(from("Trades")).not.toBeInvalid();
    // Opening is no answer to "Show results": no field takes the focus, no keyboard slides up.
    expect(to("Win rate")).not.toHaveFocus();
    expect(intoView.mock.contexts).not.toContain(to("Win rate"));
  });
});

describe("other failures", () => {
  test("without an answer the banner stands above the button, the input stays, and asking again works", async () => {
    const api = boardApi(noAnswer);
    const { user } = await renderOpen();

    await user.type(from("Trades"), "3");
    await user.click(show());

    expect(await within(sheet()).findByRole("alert")).toHaveTextContent(
      "No connection. Try again.",
    );
    expect(from("Trades")).toHaveValue("3");
    expect(from("Trades")).not.toBeInvalid();
    expect(navigation.href).toBe("/leaderboard");
    expect(show()).toBeEnabled();

    api.on(BOARD, FILTERED);
    await user.click(show());

    await closed();
    expect(navigation.href).toBe("/leaderboard?min_trades=3");
  });

  test("a 503 shows the API's sentence in the sheet", async () => {
    boardApi(
      apiError(503, "SERVICE_UNAVAILABLE", "This is temporarily unavailable. Try again shortly."),
    );
    const { user } = await renderOpen();

    await user.type(from("Trades"), "3");
    await user.click(show());

    expect(await within(sheet()).findByRole("alert")).toHaveTextContent(
      "This is temporarily unavailable. Try again shortly.",
    );
    expect(navigation.href).toBe("/leaderboard");
  });

  test("after RATE_LIMITED the button is disabled for the time the API names, with a countdown", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const api = boardApi(
        apiError(429, "RATE_LIMITED", "Too many requests. Try again later.", {
          retry_after_seconds: 30,
        }),
      );
      const { user } = await renderOpen();

      await user.type(from("Trades"), "3");
      await user.click(show());

      expect(await within(sheet()).findByRole("alert")).toHaveTextContent(
        "Too many requests. Try again later.",
      );
      expect(show()).toBeDisabled();
      expect(within(sheet()).getByText(/^Try again in \d+ s$/)).toBeInTheDocument();
      const asked = api.calls(BOARD).length;

      // Enter in a field is a submit too, and it waits like the button.
      await user.type(from("Trades"), "{Enter}");
      expect(api.calls(BOARD)).toHaveLength(asked);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(31_000);
      });

      expect(show()).toBeEnabled();
      expect(within(sheet()).queryByText(/^Try again in/)).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("closing and clearing", () => {
  test("closing the sheet takes back a question that is still open: its answer changes nothing", async () => {
    const answer = deferred();
    const api = boardApi(answer.handler);
    const { user } = await renderOpen();

    await user.type(from("Trades"), "3");
    await user.click(show());
    expect(api.calls(BOARD)).toHaveLength(2);
    await user.keyboard("{Escape}");
    await closed();

    await act(async () => {
      answer.resolve(FILTERED);
      await Promise.resolve();
    });

    expect(navigation.href).toBe("/leaderboard");
    expect(navigation.router.push).not.toHaveBeenCalled();
    expect(screen.getByText("polyterrasse")).toBeInTheDocument();
    expect(screen.queryByText("only_one")).not.toBeInTheDocument();
  });

  test("closing without Show results applies nothing, and the next opening starts from the list again", async () => {
    const api = boardApi();
    const { user } = await renderOpen("/leaderboard?min_win_rate=0.625");
    expect(from("Win rate")).toHaveValue("62.5");

    await user.type(to("Trades"), "40");
    await user.clear(from("Win rate"));
    await user.click(within(sheet()).getByRole("button", { name: "Close" }));
    await closed();

    expect(navigation.href).toBe("/leaderboard?min_win_rate=0.625");
    expect(api.calls(BOARD)).toHaveLength(1);

    await openSheet(user);

    expect(from("Win rate")).toHaveValue("62.5");
    expect(to("Trades")).toHaveValue("");
  });

  test("the fields start from the filters of the address, a win rate in percent", async () => {
    boardApi();
    await renderOpen(
      "/leaderboard?degree_level=MASTER&degree_programme=MSc+Data+Science" +
        "&min_credits_cr=100.5&max_win_rate=1&min_trades=3&max_avg_payout_ratio=2.5",
    );

    expect(level()).toHaveValue("MASTER");
    expect(programme()).toHaveValue("MSc Data Science");
    expect(programme()).toBeEnabled();
    expect(from("Tokens")).toHaveValue("100.5");
    expect(to("Tokens")).toHaveValue("");
    expect(to("Win rate")).toHaveValue("100");
    expect(from("Trades")).toHaveValue("3");
    expect(to("Payout")).toHaveValue("2.5");
    expect(clearAll()).toBeEnabled();
  });

  test("Clear all removes every filter, keeps the sort, and closes the sheet", async () => {
    const api = boardApi();
    const { user } = await renderOpen(
      "/leaderboard?sort=trades&degree_level=MASTER&min_win_rate=0.5",
    );
    await screen.findByText("only_one");

    await user.click(clearAll());

    await closed();
    expect(navigation.href).toBe("/leaderboard?sort=trades");
    expect(await screen.findByText("polyterrasse")).toBeInTheDocument();
    expect(sent(api.lastCall(BOARD))).toEqual({ sort: "trades", order: "desc" });
    expect(screen.queryByRole("list", { name: "Filters that are set" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Filter" })).toBeInTheDocument();
  });

  test("Clear all with only typed fields empties them and asks the API for nothing", async () => {
    const api = boardApi();
    const { user } = await renderOpen();
    await user.type(from("Trades"), "3");
    expect(clearAll()).toBeEnabled();

    await user.click(clearAll());

    await closed();
    expect(api.calls(BOARD)).toHaveLength(1);
    expect(navigation.router.push).not.toHaveBeenCalled();

    await openSheet(user);
    expect(from("Trades")).toHaveValue("");
  });

  test.each(["phone", "wide"] as const)(
    "at %s width the focus moves into the sheet when it opens, and back to the Filter button when it closes",
    async (viewport) => {
      setViewport(viewport);
      boardApi();
      const { user } = await renderOpen();

      await waitFor(() => expect(sheet().contains(document.activeElement)).toBe(true));

      await user.keyboard("{Escape}");
      await closed();

      await waitFor(() => expect(screen.getByRole("button", { name: "Filter" })).toHaveFocus());
    },
  );

  test("turning the phone across 768 px keeps what was typed, the sheet and the focus", async () => {
    boardApi();
    const { user } = await renderOpen();
    await user.selectOptions(level(), "Master");
    await user.type(from("Trades"), "3");

    // The bottom sheet gives way to the dialog, which mounts its content afresh.
    act(() => setViewport("wide"));

    const dialog = await screen.findByRole("dialog", { name: "Filter" });
    expect(dialog).toHaveAttribute("data-slot", "dialog-content");
    expect(level()).toHaveValue("MASTER");
    expect(from("Trades")).toHaveValue("3");
    // The sheet is still open: the focus stays inside it and is not handed back.
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
    expect(navigation.href).toBe("/leaderboard");
  });

  test("the bottom sheet takes the focus itself, not a field: no keyboard slides up over it", async () => {
    boardApi();
    await renderOpen();

    await waitFor(() => expect(sheet()).toHaveFocus());
  });

  test("after Show results the focus is back on the Filter button, which now counts the filters", async () => {
    boardApi();
    const { user } = await renderOpen();

    await user.type(from("Trades"), "3");
    await user.click(show());
    await closed();

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Filter, 1 set" })).toHaveFocus(),
    );
  });
});
