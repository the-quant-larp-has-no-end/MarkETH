import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, test } from "vitest";

import { keys } from "@/lib/api/keys";
import type { LeaderboardResponse } from "@/lib/api/schema";
import { EN_DASH, TIMES } from "@/lib/format";
import { config, leaderboard } from "@/test/fixtures";
import { json, mockApi, type Route } from "@/test/http";
import { setViewport } from "@/test/media";
import { followHistoryWrites, navigation } from "@/test/navigation";
import { createTestQueryClient, renderWithProviders } from "@/test/render";

import { LeaderboardScreen } from "./leaderboard-screen";
import { player, you } from "./test-players";

/**
 * The statistics of a player, as the Leaderboard opens them: which row was selected and
 * what the list holds are part of what the sheet shows, so the tests go through the screen.
 */

const BOARD: Route = "GET /api/leaderboard";

const board = (overrides: Partial<LeaderboardResponse> = {}) => json(200, leaderboard(overrides));

const places = () => [...document.querySelectorAll<HTMLElement>("[data-slot=podium-place]")];
const pinnedBar = () => document.querySelector("[data-slot=your-row]") as HTMLElement;
const list = () => document.querySelector("[data-slot=ranking]") as HTMLElement;
/** The button of a row below the podium. */
const rowButton = (username: string) =>
  within(list()).getByText(username).closest("button") as HTMLButtonElement;
const sheet = (username: string) => screen.getByRole("dialog", { name: username });
/** The figure that stands beside a name in the statistics. */
const figure = (inside: HTMLElement, name: string) =>
  within(inside).getByText(name).parentElement!.querySelector("dd") as HTMLElement;

/** The bottom sheet takes half a second to slide away before it is gone. */
async function closed() {
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument(), {
    timeout: 3000,
  });
}

beforeEach(() => {
  navigation.go("/leaderboard");
  followHistoryWrites();
});

describe("what can be selected", () => {
  test("a row is a button named by what it shows, and says that it opens a dialog", async () => {
    mockApi({ [BOARD]: board() });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("mensa_fan");

    const row = rowButton("mensa_fan");
    expect(row).toHaveAttribute("type", "button");
    expect(row).toHaveAttribute("aria-haspopup", "dialog");
    expect(row).toHaveAccessibleName(
      /Rank\s*4\s*mensa_fan\s*BSc Computer Science\s*120\.25 tokens/,
    );
    // The whole row is the target: at least 56 px tall and as wide as the list.
    expect(row).toHaveClass("min-h-14", "w-full");
    expect(row.parentElement).toHaveAttribute("data-slot", "row");
  });

  test("a podium place is a button named by its rank, its player and its figure", async () => {
    mockApi({ [BOARD]: board() });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("polyterrasse");

    const [first, , third] = places().map((place) => within(place).getByRole("button"));
    expect(first).toHaveAttribute("aria-haspopup", "dialog");
    expect(first).toHaveAccessibleName(/First place\s*1st\s*polyterrasse\s*1,204\.5 tokens/);
    expect(third).toHaveAccessibleName(/Third place\s*3rd\s*anna_m\s*You\s*240 tokens/);
    // A list of three, read first, second, third: each place holds its one button.
    const items = within(screen.getByRole("list", { name: "Top three" })).getAllByRole("listitem");
    expect(
      items.map((item) => within(item).getAllByRole("button")).map((found) => found.length),
    ).toEqual([1, 1, 1]);
  });

  test("an empty place of the podium is no button", async () => {
    mockApi({ [BOARD]: board({ items: [player(1, "polyterrasse"), you(2)], viewer: you(2) }) });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("polyterrasse");

    expect(places()[2]).toHaveAttribute("data-empty");
    expect(places()[2]!.querySelector("button")).not.toBeInTheDocument();
  });

  test("the pinned row is a button too", async () => {
    mockApi({
      [BOARD]: board({
        items: [player(1, "a"), player(2, "b"), player(3, "c"), player(4, "d")],
        viewer: you(1204, { credits_cr: "12.5", balance_cr: "12.5", positions_value_cr: "0" }),
      }),
    });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("a");

    const row = within(pinnedBar()).getByRole("button");
    expect(row).toHaveAttribute("aria-haspopup", "dialog");
    expect(row).toHaveAccessibleName(
      /Rank\s*1,204\s*anna_m\s*You\s*BSc Computer Science\s*12\.5 tokens/,
    );
  });
});

describe("the statistics of a player", () => {
  test("a tap on a row opens them, built from that row alone: the API is asked for nothing", async () => {
    const api = mockApi({ [BOARD]: board() });
    const { user } = renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("mensa_fan");
    const asked = api.requests.length;

    await user.click(rowButton("mensa_fan"));

    const dialog = await screen.findByRole("dialog", { name: "mensa_fan" });
    expect(within(dialog).getByRole("heading", { name: "mensa_fan" })).toBeInTheDocument();
    // The degree: the level by its name from /config, and the programme as sent.
    expect(dialog).toHaveAccessibleDescription("Bachelor · BSc Computer Science");
    expect(figure(dialog, "Rank in this list")).toHaveTextContent("4");
    // The tokens are the API's sum, and its two parts stand under it.
    expect(figure(dialog, "Tokens")).toHaveTextContent("120.25 tokens");
    expect(figure(dialog, "In the wallet")).toHaveTextContent("100 tokens");
    expect(figure(dialog, "In open positions")).toHaveTextContent("20.25 tokens");
    expect(figure(dialog, "Win rate")).toHaveTextContent("62.5%");
    expect(figure(dialog, "Payout")).toHaveTextContent(`1.35${TIMES}`);
    expect(figure(dialog, "Trades")).toHaveTextContent("1");
    expect(figure(dialog, "Resolved markets")).toHaveTextContent("8");
    // Nothing was asked: every figure is a field of the row.
    expect(api.requests).toHaveLength(asked);
    // No white button, no real name, no address.
    expect(dialog.querySelector("[data-variant=primary]")).not.toBeInTheDocument();
    expect(dialog).not.toHaveTextContent(/@ethz\.ch/);
    expect(dialog).not.toHaveTextContent("Anna Muster");
  });

  test("the figures are in the order the plan names them, the two parts set in under Tokens", async () => {
    mockApi({ [BOARD]: board() });
    const { user } = renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("mensa_fan");

    await user.click(rowButton("mensa_fan"));

    const dialog = await screen.findByRole("dialog", { name: "mensa_fan" });
    const names = [...dialog.querySelectorAll("dt")];
    expect(names.map((name) => name.textContent)).toEqual([
      "Rank in this list",
      "Tokens",
      "In the wallet",
      "In open positions",
      "Win rate",
      "Payout",
      "Trades",
      "Resolved markets",
    ]);
    expect(names.map((name) => name.parentElement!.classList.contains("pl-4"))).toEqual([
      false,
      false,
      true,
      true,
      false,
      false,
      false,
      false,
    ]);
  });

  test("the three token figures are shown with every digit, as the strings that arrived", async () => {
    mockApi({
      [BOARD]: board({
        items: [
          player(1, "a"),
          player(2, "b"),
          player(3, "c"),
          player(4, "whale", {
            credits_cr: "123456789012345678901234.999999",
            balance_cr: "123456789012345678901234.5",
            positions_value_cr: "0.499999",
          }),
        ],
        viewer: null,
      }),
    });
    const { user } = renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("whale");

    await user.click(rowButton("whale"));

    const dialog = await screen.findByRole("dialog", { name: "whale" });
    expect(figure(dialog, "Tokens")).toHaveTextContent("123,456,789,012,345,678,901,234.999999");
    expect(figure(dialog, "In the wallet")).toHaveTextContent("123,456,789,012,345,678,901,234.5");
    expect(figure(dialog, "In open positions")).toHaveTextContent("0.499999");
    // The row behind it shows the short figure of a list.
    expect(rowButton("whale")).toHaveTextContent("123,456,789,012,345,678,901,234.99 tokens");
  });

  test("a player without a resolved market has no rates: none yet, and zero markets", async () => {
    mockApi({ [BOARD]: board() });
    const { user } = renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("polyterrasse");

    // The viewer stands third, on the podium.
    await user.click(within(places()[2]!).getByRole("button"));

    const dialog = await screen.findByRole("dialog", { name: "anna_m" });
    expect(dialog).toHaveAccessibleDescription("You · Bachelor · BSc Computer Science");
    expect(figure(dialog, "Rank in this list")).toHaveTextContent("3");
    expect(figure(dialog, "Tokens")).toHaveTextContent("240 tokens");
    expect(figure(dialog, "In the wallet")).toHaveTextContent("200 tokens");
    expect(figure(dialog, "In open positions")).toHaveTextContent("40 tokens");
    for (const name of ["Win rate", "Payout"]) {
      expect(figure(dialog, name)).toHaveTextContent(EN_DASH);
      expect(within(figure(dialog, name)).getByText("none yet")).toHaveClass("sr-only");
    }
    expect(figure(dialog, "Trades")).toHaveTextContent("4");
    expect(figure(dialog, "Resolved markets")).toHaveTextContent("0");
  });

  test("a podium place opens the statistics of the player who stands on it", async () => {
    mockApi({ [BOARD]: board() });
    const { user } = renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("polyterrasse");

    // Second place stands at the left, and is still the second of the list.
    await user.click(within(places()[1]!).getByRole("button"));

    const dialog = await screen.findByRole("dialog", { name: "cab_coffee" });
    expect(dialog).toHaveAccessibleDescription("Master · MSc Data Science");
    expect(figure(dialog, "Rank in this list")).toHaveTextContent("2");
    expect(figure(dialog, "Tokens")).toHaveTextContent("980 tokens");
    expect(figure(dialog, "In the wallet")).toHaveTextContent("980 tokens");
    expect(figure(dialog, "In open positions")).toHaveTextContent("0 tokens");
  });

  test("the pinned row opens the viewer's own statistics, from the viewer row of the answer", async () => {
    const yours = you(1204, {
      credits_cr: "12.5",
      balance_cr: "2.5",
      positions_value_cr: "10",
      win_rate: 0.4,
    });
    const api = mockApi({
      [BOARD]: board({
        items: [player(1, "a"), player(2, "b"), player(3, "c"), player(4, "d")],
        next_cursor: "4",
        viewer: yours,
      }),
    });
    const { user } = renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("a");
    const asked = api.calls(BOARD).length;

    await user.click(within(pinnedBar()).getByRole("button"));

    const dialog = await screen.findByRole("dialog", { name: "anna_m" });
    expect(figure(dialog, "Rank in this list")).toHaveTextContent("1,204");
    expect(figure(dialog, "Tokens")).toHaveTextContent("12.5 tokens");
    expect(figure(dialog, "In the wallet")).toHaveTextContent("2.5 tokens");
    expect(figure(dialog, "In open positions")).toHaveTextContent("10 tokens");
    expect(figure(dialog, "Win rate")).toHaveTextContent("40.0%");
    // The row is not loaded, and opening its statistics loads nothing.
    expect(api.calls(BOARD)).toHaveLength(asked);
  });

  test("the rank is the one of the list as it is shown, under its sort and its filters", async () => {
    navigation.go("/leaderboard?sort=trades&order=asc&degree_level=MASTER");
    mockApi({
      [BOARD]: board({
        items: [player(1, "few_trades", { trades: 2 }), player(2, "more_trades", { trades: 9 })],
        viewer: null,
      }),
    });
    const { user } = renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("more_trades");

    await user.click(rowButton("more_trades"));

    const dialog = await screen.findByRole("dialog", { name: "more_trades" });
    expect(figure(dialog, "Rank in this list")).toHaveTextContent("2");
    expect(figure(dialog, "Trades")).toHaveTextContent("9");
  });

  test("a level that /config does not list leaves the programme to speak for itself", async () => {
    mockApi({
      [BOARD]: board({
        items: [
          player(1, "a"),
          player(2, "b"),
          player(3, "c"),
          player(4, "guest", {
            degree_level: "CONTINUING_EDUCATION",
            degree_programme: "CAS Applied Machine Learning & Information Processing",
          }),
        ],
        viewer: null,
      }),
    });
    const { user } = renderWithProviders(<LeaderboardScreen />, {
      config: config({ degrees: [{ level: "BACHELOR", name: "Bachelor", programmes: [] }] }),
    });
    await screen.findByText("guest");

    await user.click(rowButton("guest"));

    expect(await screen.findByRole("dialog", { name: "guest" })).toHaveAccessibleDescription(
      "CAS Applied Machine Learning & Information Processing",
    );
  });

  test("it holds no copy of the row: when the list is read again, its figures follow", async () => {
    const client = createTestQueryClient();
    let answer = board();
    mockApi({ [BOARD]: () => answer });
    const { user } = renderWithProviders(<LeaderboardScreen />, { client });
    await screen.findByText("mensa_fan");
    await user.click(rowButton("mensa_fan"));
    const dialog = await screen.findByRole("dialog", { name: "mensa_fan" });
    expect(figure(dialog, "Tokens")).toHaveTextContent("120.25 tokens");

    // A trade moved the player up while the sheet was open.
    answer = board({
      items: [
        player(1, "polyterrasse"),
        player(2, "mensa_fan", {
          credits_cr: "1100.75",
          balance_cr: "100.5",
          positions_value_cr: "1000.25",
          trades: 2,
        }),
        player(3, "cab_coffee"),
        you(4),
      ],
      viewer: you(4),
    });
    await act(() => client.invalidateQueries({ queryKey: keys.leaderboard.all }));

    await waitFor(() => expect(figure(dialog, "Tokens")).toHaveTextContent("1,100.75 tokens"));
    expect(figure(dialog, "Rank in this list")).toHaveTextContent("2");
    expect(figure(dialog, "In the wallet")).toHaveTextContent("100.5 tokens");
    expect(figure(dialog, "In open positions")).toHaveTextContent("1,000.25 tokens");
    expect(figure(dialog, "Trades")).toHaveTextContent("2");
  });
});

describe("a player who leaves the list", () => {
  test("their statistics close, and do not come back by themselves when the player is listed again", async () => {
    const client = createTestQueryClient();
    let answer = board();
    mockApi({ [BOARD]: () => answer });
    const { user } = renderWithProviders(<LeaderboardScreen />, { client });
    await screen.findByText("mensa_fan");
    await user.click(rowButton("mensa_fan"));
    await screen.findByRole("dialog", { name: "mensa_fan" });

    answer = board({ items: [player(1, "polyterrasse"), you(2)], viewer: you(2) });
    await act(() => client.invalidateQueries({ queryKey: keys.leaderboard.all }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    answer = board();
    await act(() => client.invalidateQueries({ queryKey: keys.leaderboard.all }));

    await waitFor(() => expect(within(list()).getByText("mensa_fan")).toBeInTheDocument());
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    // The row opens them again as it did before.
    await user.click(rowButton("mensa_fan"));
    expect(await screen.findByRole("dialog", { name: "mensa_fan" })).toBeInTheDocument();
  });
});

describe("opening and closing", () => {
  test.each([
    ["Enter", "{Enter}"],
    ["Space", " "],
  ])("the keyboard opens a row with %s", async (_key, typed) => {
    mockApi({ [BOARD]: board() });
    const { user } = renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("mensa_fan");

    act(() => rowButton("mensa_fan").focus());
    await user.keyboard(typed);

    expect(await screen.findByRole("dialog", { name: "mensa_fan" })).toBeInTheDocument();
  });

  test("the keyboard reaches the podium places, the rows and the pinned row with the Tab key, and opens each", async () => {
    mockApi({
      [BOARD]: board({
        items: [player(1, "a"), player(2, "b"), player(3, "c"), player(4, "d")],
        next_cursor: "4",
        viewer: you(9),
      }),
    });
    const { user } = renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("d");

    // After the head of the screen: first, second and third place, as the podium is read
    // out, then the row, then the pinned row.
    const [first, second, third] = places().map((place) => within(place).getByRole("button"));
    const order = [first, second, third, rowButton("d"), within(pinnedBar()).getByRole("button")];
    act(() => screen.getByRole("button", { name: "Payout" }).focus());
    for (const stop of order) {
      await user.tab();
      expect(stop).toHaveFocus();
    }

    await user.keyboard("{Enter}");
    expect(await screen.findByRole("dialog", { name: "anna_m" })).toBeInTheDocument();
  });

  test("the close cross closes them, and the focus returns to the row that opened them", async () => {
    mockApi({ [BOARD]: board() });
    const { user } = renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("mensa_fan");
    await user.click(rowButton("mensa_fan"));
    const dialog = await screen.findByRole("dialog", { name: "mensa_fan" });

    await user.click(within(dialog).getByRole("button", { name: "Close" }));

    await closed();
    await waitFor(() => expect(rowButton("mensa_fan")).toHaveFocus());
    // The list is as it was: closing asks for nothing and changes no address.
    expect(navigation.href).toBe("/leaderboard");
  });

  test("Escape closes them too, and another player can be opened after", async () => {
    mockApi({ [BOARD]: board() });
    const { user } = renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("mensa_fan");
    await user.click(rowButton("mensa_fan"));
    await screen.findByRole("dialog", { name: "mensa_fan" });

    await user.keyboard("{Escape}");
    await closed();
    await user.click(within(places()[0]!).getByRole("button"));

    expect(await screen.findByRole("dialog", { name: "polyterrasse" })).toBeInTheDocument();
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
  });

  test("below 768 px they are a bottom sheet with the close cross", async () => {
    mockApi({ [BOARD]: board() });
    const { user } = renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("mensa_fan");

    await user.click(rowButton("mensa_fan"));

    expect(sheet("mensa_fan")).toHaveAttribute("data-slot", "drawer-content");
    expect(within(sheet("mensa_fan")).getByRole("button", { name: "Close" })).toBeEnabled();
  });
});

describe("from 768 px", () => {
  beforeEach(() => setViewport("wide"));

  const answer = () =>
    board({
      items: [
        player(1, "a"),
        player(2, "b"),
        player(3, "c"),
        player(4, "mensa_fan", {
          credits_cr: "120.25",
          balance_cr: "100",
          positions_value_cr: "20.25",
          trades: 1204,
          degree_programme: "MSc Data Science",
          degree_level: "MASTER",
        }),
        you(5),
      ],
      viewer: you(5),
    });
  const tableRow = (username: string) =>
    within(screen.getByRole("table", { name: "Ranking" }))
      .getByText(username)
      .closest("tr") as HTMLTableRowElement;

  test("they are a dialog with the close cross", async () => {
    mockApi({ [BOARD]: answer() });
    const { user } = renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("mensa_fan");

    await user.click(within(tableRow("mensa_fan")).getByRole("button"));

    const dialog = await screen.findByRole("dialog", { name: "mensa_fan" });
    expect(dialog).toHaveAttribute("data-slot", "dialog-content");
    expect(within(dialog).getByRole("button", { name: "Close" })).toBeEnabled();
    expect(dialog).toHaveAccessibleDescription("Master · MSc Data Science");
    expect(figure(dialog, "Trades")).toHaveTextContent("1,204");
  });

  test("the username of a table row is its button, named by the player; the keyboard opens it", async () => {
    mockApi({ [BOARD]: answer() });
    const { user } = renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("mensa_fan");

    const button = within(tableRow("mensa_fan")).getByRole("button");
    expect(button).toHaveAttribute("aria-haspopup", "dialog");
    expect(button).toHaveAccessibleName(/mensa_fan\s*MSc Data Science/);
    expect(within(tableRow("anna_m")).getByRole("button")).toHaveAccessibleName(
      /anna_m\s*You\s*BSc Computer Science/,
    );

    act(() => button.focus());
    await user.keyboard("{Enter}");

    expect(await screen.findByRole("dialog", { name: "mensa_fan" })).toBeInTheDocument();
  });

  test("a tap anywhere on a table row opens it once", async () => {
    mockApi({ [BOARD]: answer() });
    const { user } = renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("mensa_fan");
    expect(tableRow("mensa_fan")).toHaveClass("cursor-pointer");

    // On a figure, not on the username.
    await user.click(within(tableRow("mensa_fan")).getAllByRole("cell")[4]!);

    expect(await screen.findByRole("dialog", { name: "mensa_fan" })).toBeInTheDocument();
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
  });

  test("a tap on a column heading sorts and opens nothing", async () => {
    mockApi({ [BOARD]: answer() });
    const { user } = renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("mensa_fan");

    await user.click(
      within(screen.getByRole("columnheader", { name: "Trades" })).getByRole("button"),
    );

    expect(navigation.href).toBe("/leaderboard?sort=trades");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
