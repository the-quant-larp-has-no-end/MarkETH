import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";

import type { LeaderboardResponse } from "@/lib/api/schema";
import { REDUCED_MOTION_QUERY } from "@/lib/design";
import { EN_DASH, TIMES } from "@/lib/format";
import { leaderboard } from "@/test/fixtures";
import {
  apiError,
  deferred,
  json,
  type MockRequest,
  mockApi,
  noAnswer,
  type Route,
} from "@/test/http";
import { setMediaQuery, setViewport } from "@/test/media";
import { followHistoryWrites, navigation } from "@/test/navigation";
import { intersect } from "@/test/observers";
import { renderWithProviders } from "@/test/render";

import { LeaderboardScreen } from "./leaderboard-screen";
import { player, you } from "./test-players";

const BOARD: Route = "GET /api/leaderboard";

const UNAVAILABLE = apiError(
  503,
  "SERVICE_UNAVAILABLE",
  "This is temporarily unavailable. Try again shortly.",
);

const board = (overrides: Partial<LeaderboardResponse>) => json(200, leaderboard(overrides));

/** What a request asked for, by parameter. */
const sent = (request: MockRequest | undefined) => Object.fromEntries(request?.query ?? []);

const podium = () => document.querySelector("[data-slot=podium]") as HTMLElement | null;
const places = () => [...document.querySelectorAll<HTMLElement>("[data-slot=podium-place]")];
const pinnedBar = () => document.querySelector("[data-slot=your-row]") as HTMLElement | null;
const viewerRow = () => document.querySelector(".viewer-row") as HTMLElement | null;
const main = () => document.querySelector("[data-slot=screen]") as HTMLElement;
/** One of the sort chips. From 768 px a column heading carries the same name. */
const sortChips = () => screen.getByRole("group", { name: "Sort by" });
const chip = (name: string) => within(sortChips()).getByRole("button", { name });
/** A trophy: the only image with a name. The token icon is decoration. */
const trophy = (inside: Element) => within(inside as HTMLElement).queryByRole("img");
/** The usernames of the rows below the podium, top to bottom. */
const listed = () =>
  [...document.querySelectorAll("[data-slot=ranking] [data-slot=row] span[title].type-body")].map(
    (name) => name.textContent,
  );
/** The row of a player below the podium. */
const rowOf = (username: string) =>
  within(document.querySelector("[data-slot=ranking]") as HTMLElement)
    .getByText(username)
    .closest("[data-slot=row]") as HTMLElement;

/** An IntersectionObserver the test speaks for: it says what is on screen, and what is not. */
class Watcher {
  static all = new Set<Watcher>();
  readonly targets = new Set<Element>();

  constructor(
    readonly callback: IntersectionObserverCallback,
    readonly options: IntersectionObserverInit = {},
  ) {
    Watcher.all.add(this);
  }

  observe(target: Element): void {
    this.targets.add(target);
  }

  unobserve(target: Element): void {
    this.targets.delete(target);
  }

  disconnect(): void {
    this.targets.clear();
    Watcher.all.delete(this);
  }

  takeRecords(): IntersectionObserverEntry[] {
    return [];
  }
}

function watching(target: Element): Watcher[] {
  return [...Watcher.all].filter((watcher) => watcher.targets.has(target));
}

/** Tells whoever watches `target` how much of it is in view: 0 is scrolled away, 1 all of it. */
function report(target: Element, share: number): void {
  act(() => {
    for (const watcher of watching(target)) {
      watcher.callback(
        [
          {
            target,
            isIntersecting: share > 0,
            intersectionRatio: share,
          } as IntersectionObserverEntry,
        ],
        watcher as unknown as IntersectionObserver,
      );
    }
  });
}

beforeEach(() => {
  navigation.go("/leaderboard");
  followHistoryWrites();
  Watcher.all.clear();
});

describe("how the list opens", () => {
  test("on Tokens, Highest first: the request names both, and no page size", async () => {
    const api = mockApi({ [BOARD]: board({}) });
    renderWithProviders(<LeaderboardScreen />);

    await screen.findByText("polyterrasse");
    expect(api.calls(BOARD)).toHaveLength(1);
    expect(sent(api.lastCall(BOARD))).toEqual({ sort: "credits", order: "desc" });
    expect(api.lastCall(BOARD)?.headers.get("Accept")).toBe("application/json");
  });

  test("the title is the one h1, with the line that states the measure", async () => {
    mockApi({ [BOARD]: board({}) });
    renderWithProviders(<LeaderboardScreen />);

    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading).toHaveTextContent("Leaderboard");
    expect(heading).toHaveClass("type-h1");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    // Tokens are the wallet and the open positions together: the line says so, and the
    // figure is the API's `credits_cr`.
    const caption = screen.getByText("Tokens in the wallet plus the value of open positions.");
    expect(caption).toHaveClass("type-caption", "text-muted-foreground");
    expect(caption).toHaveAttribute("aria-live", "polite");
    await screen.findByText("polyterrasse");
  });

  test("the title row holds the title, the order button named after the active order, and Filter", async () => {
    mockApi({ [BOARD]: board({}) });
    renderWithProviders(<LeaderboardScreen />);

    const row = document.querySelector("[data-slot=title-row]") as HTMLElement;
    expect(within(row).getByRole("heading", { level: 1 })).toHaveTextContent("Leaderboard");
    const order = within(row).getByRole("button", { name: "Highest first" });
    expect(order).toHaveAttribute("data-variant", "secondary");
    expect(order).toHaveClass("size-11");
    const filter = within(row).getByRole("button", { name: "Filter" });
    expect(filter).toHaveAttribute("data-variant", "secondary");
    expect(filter).toHaveClass("h-11");
    // The title is never cut: the two buttons give way to it and wrap under it.
    expect(row).toHaveClass("flex-wrap");
    expect(within(row).getByRole("heading")).toHaveClass("whitespace-nowrap");
    // No filter is set: no count, no chips, and no white button on the screen.
    expect(screen.queryByRole("list", { name: "Filters that are set" })).not.toBeInTheDocument();
    expect(document.querySelectorAll("[data-variant=primary]")).toHaveLength(0);
    await screen.findByText("polyterrasse");
  });

  test("the sort chips stand in a row of their own: four of them, one pressed, as a grid that never scrolls", async () => {
    mockApi({ [BOARD]: board({}) });
    renderWithProviders(<LeaderboardScreen />);

    const chips = within(sortChips()).getAllByRole("button");
    expect(chips.map((button) => button.textContent)).toEqual([
      "Tokens",
      "Win rate",
      "Trades",
      "Payout",
    ]);
    expect(chips.map((button) => button.getAttribute("aria-pressed"))).toEqual([
      "true",
      "false",
      "false",
      "false",
    ]);
    expect(sortChips()).toHaveAttribute("data-layout", "grid");
    // Two by two on a screen under 344 px wide.
    expect(sortChips()).toHaveClass("max-[344px]:grid-cols-2");
    expect(sortChips()).not.toHaveClass("overflow-x-auto");
    // Nothing shares their row: the two buttons are in the title row.
    const row = document.querySelector("[data-slot=sort-chips]") as HTMLElement;
    expect(within(row).getAllByRole("button")).toHaveLength(4);
    expect(row.contains(screen.getByRole("button", { name: "Filter" }))).toBe(false);
    expect(row.contains(screen.getByRole("button", { name: "Highest first" }))).toBe(false);
    await screen.findByText("polyterrasse");
  });

  test("the parts of the head follow one another: title row, measure line, sort chips, set filters, podium, ranking", async () => {
    navigation.go("/leaderboard?min_trades=1");
    mockApi({ [BOARD]: board({}) });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("polyterrasse");

    const parts = [
      document.querySelector("[data-slot=title-row]")!,
      document.querySelector("[data-slot=measure-line]")!,
      document.querySelector("[data-slot=sort-chips]")!,
      screen.getByRole("list", { name: "Filters that are set" }),
      podium()!,
      rowOf("mensa_fan"),
    ];
    parts.slice(1).forEach((part, index) => {
      expect(parts[index]!.compareDocumentPosition(part)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    });
  });
});

describe("the four states", () => {
  test("loading: a skeleton podium and five skeleton rows", async () => {
    const answer = deferred();
    mockApi({ [BOARD]: answer.handler });
    renderWithProviders(<LeaderboardScreen />);

    expect(document.querySelector("[data-slot=podium-skeleton]")).toBeInTheDocument();
    expect(document.querySelectorAll("[data-slot=row-skeletons] > li")).toHaveLength(5);
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
    expect(podium()).not.toBeInTheDocument();

    answer.resolve(board({}));

    expect(await screen.findByText("polyterrasse")).toBeInTheDocument();
    expect(document.querySelector("[data-slot=podium-skeleton]")).not.toBeInTheDocument();
    expect(document.querySelectorAll("[data-slot=row-skeletons] > li")).toHaveLength(0);
  });

  test("loading a Lowest first list has no podium to stand in for", () => {
    navigation.go("/leaderboard?order=asc");
    mockApi({ [BOARD]: deferred().handler });
    renderWithProviders(<LeaderboardScreen />);

    expect(document.querySelector("[data-slot=podium-skeleton]")).not.toBeInTheDocument();
    expect(document.querySelectorAll("[data-slot=row-skeletons] > li")).toHaveLength(5);
  });

  test("error: a banner with the API's message and Try again, which asks again", async () => {
    const api = mockApi({ [BOARD]: UNAVAILABLE });
    const { user } = renderWithProviders(<LeaderboardScreen />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This is temporarily unavailable. Try again shortly.",
    );
    expect(podium()).not.toBeInTheDocument();
    // The controls stay: the view can still be changed.
    expect(chip("Tokens")).toBeInTheDocument();

    api.on(BOARD, board({}));
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByText("polyterrasse")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  test("no connection has its own sentence", async () => {
    mockApi({ [BOARD]: noAnswer });
    renderWithProviders(<LeaderboardScreen />);

    expect(await screen.findByRole("alert")).toHaveTextContent("No connection. Try again.");
  });

  test("empty with filters: one sentence and Clear all, which removes every filter", async () => {
    navigation.go("/leaderboard?sort=trades&degree_level=DOCTORATE&min_trades=900");
    const api = mockApi({
      [BOARD]: (request) =>
        request.query.has("min_trades")
          ? board({ items: [], viewer: null, next_cursor: null })
          : board({}),
    });
    const { user } = renderWithProviders(<LeaderboardScreen />);

    expect(await screen.findByText("Nobody matches these filters.")).toBeInTheDocument();
    expect(podium()).not.toBeInTheDocument();
    expect(pinnedBar()).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Clear all" }));

    expect(await screen.findByText("polyterrasse")).toBeInTheDocument();
    // The sort is no filter: it stays.
    expect(navigation.href).toBe("/leaderboard?sort=trades");
    expect(sent(api.lastCall(BOARD))).toEqual({ sort: "trades", order: "desc" });
    expect(screen.queryByText("Nobody matches these filters.")).not.toBeInTheDocument();
  });
});

describe("the podium", () => {
  test("three columns for the rows of rank 1, 2 and 3, each with its trophy, its rank as text, the username and the figure", async () => {
    mockApi({ [BOARD]: board({}) });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("polyterrasse");

    const columns = within(screen.getByRole("list", { name: "Top three" })).getAllByRole(
      "listitem",
    );
    // Read out first, second, third.
    expect(columns).toHaveLength(3);
    const [first, second, third] = columns as [HTMLElement, HTMLElement, HTMLElement];

    // 96 px for first place and 72 px for the others, and never wider than the column.
    const gold = within(first).getByRole("img", { name: "First place" });
    expect(gold.parentElement).toHaveClass("w-24", "max-w-full", "aspect-square");
    expect(first).toHaveTextContent("1st");
    // One line, cut with an ellipsis when it is too long, and whole in its title.
    const name = within(first).getByText("polyterrasse");
    expect(name).toHaveClass("type-label", "md:type-h3", "truncate", "w-full");
    expect(name).toHaveAttribute("title", "polyterrasse");
    // On a phone every figure of the podium is in `number`.
    expect(first.querySelector("[data-slot=amount]")).toHaveClass("type-number");
    expect(first.querySelector("[data-slot=amount]")).not.toHaveClass("type-number-lg");
    expect(first).toHaveTextContent("1,204.5 tokens");

    const silver = within(second).getByRole("img", { name: "Second place" });
    expect(silver.parentElement).toHaveClass("w-18", "max-w-full");
    expect(second).toHaveTextContent("2nd");
    expect(second).toHaveTextContent("cab_coffee");
    expect(second.querySelector("[data-slot=amount]")).toHaveClass("type-number");
    expect(second).toHaveTextContent("980 tokens");

    const bronze = within(third).getByRole("img", { name: "Third place" });
    expect(bronze.parentElement).toHaveClass("w-18", "max-w-full");
    expect(third).toHaveTextContent("3rd");
    expect(third).toHaveTextContent("240 tokens");
  });

  test("a figure never leaves its column: it wraps inside it and is never cut", async () => {
    mockApi({
      [BOARD]: board({
        items: [player(1, "whale", { credits_cr: "1234567.89" }), you(2)],
        viewer: you(2),
      }),
    });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("whale");

    const figure = places()[0]!.querySelector("[data-slot=amount]")!;
    expect(figure).toHaveTextContent("1,234,567.89");
    expect(figure).toHaveClass("max-w-full", "flex-wrap", "whitespace-normal");
    expect(figure).not.toHaveClass("whitespace-nowrap", "truncate");
    // The three columns are equal, whatever stands in them.
    expect(screen.getByRole("list", { name: "Top three" })).toHaveClass("grid-cols-3", "gap-2");
    for (const place of places()) expect(place).toHaveClass("min-w-0");
  });

  test("first place stands in the middle, second on its left, third on its right, on one card", async () => {
    mockApi({ [BOARD]: board({}) });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("polyterrasse");

    const [first, second, third] = places();
    expect(second).toHaveClass("order-1");
    expect(first).toHaveClass("order-2");
    expect(third).toHaveClass("order-3");
    expect(podium()).toHaveClass("bg-card", "border", "rounded-lg");
    expect(podium()).toHaveClass("pb-0");
    // The steps are flat --raised blocks of three heights with square corners.
    const steps = places().map((place) => place.querySelector("[data-slot=podium-step]")!);
    expect(steps.map((step) => step.className)).toEqual([
      expect.stringContaining("h-12"),
      expect.stringContaining("h-8"),
      expect.stringContaining("h-5"),
    ]);
    for (const step of steps) {
      expect(step).toHaveClass("bg-raised");
      expect(step.className).not.toMatch(/rounded/);
    }
  });

  test("the rows from fourth place stand below it, as leaderboard rows", async () => {
    mockApi({ [BOARD]: board({}) });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("mensa_fan");

    expect(listed()).toEqual(["mensa_fan"]);
    const row = rowOf("mensa_fan");
    expect(row).toHaveTextContent("Rank 4");
    expect(row).toHaveTextContent("BSc Computer Science");
    expect(row).toHaveTextContent("120.25 tokens");
    expect(trophy(row)).not.toBeInTheDocument();
  });

  test("a place stays empty, as the outline of its step, when the list has no row of that rank", async () => {
    mockApi({
      [BOARD]: board({ items: [player(1, "polyterrasse"), you(2)], viewer: you(2) }),
    });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("polyterrasse");

    const [, , third] = places();
    expect(third).toHaveAttribute("data-empty");
    expect(third).toHaveAttribute("aria-hidden", "true");
    expect(trophy(third!)).not.toBeInTheDocument();
    expect(third).toHaveTextContent("");
    expect(third!.firstElementChild).toHaveClass("border", "border-subtle", "h-5");
    expect(third!.firstElementChild).not.toHaveClass("bg-raised");
    expect(screen.getAllByRole("img", { name: /place$/ })).toHaveLength(2);
  });

  test("a row without a figure for the sorted measure leaves its place empty and is listed below", async () => {
    navigation.go("/leaderboard?sort=win_rate");
    mockApi({
      [BOARD]: board({
        items: [
          player(1, "polyterrasse", { win_rate: 0.75 }),
          player(2, "fresh_one", { win_rate: null, avg_payout_ratio: null }),
          you(3, { win_rate: null, avg_payout_ratio: null }),
        ],
        viewer: you(3, { win_rate: null, avg_payout_ratio: null }),
      }),
    });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("polyterrasse");

    const [first, second, third] = places();
    expect(first).toHaveTextContent("75.0%");
    expect(second).toHaveAttribute("data-empty");
    expect(third).toHaveAttribute("data-empty");
    expect(listed()).toEqual(["fresh_one", "anna_m"]);
    // The missing rate is an en dash, read out as "none yet".
    const row = rowOf("fresh_one");
    expect(row).toHaveTextContent(EN_DASH);
    expect(within(row).getByText("none yet")).toHaveClass("sr-only");
    expect(trophy(row)).not.toBeInTheDocument();
  });

  test("when the first row has no figure, the campus line stands above an empty podium", async () => {
    navigation.go("/leaderboard?sort=avg_payout_ratio");
    const nobody = { win_rate: null, avg_payout_ratio: null };
    mockApi({
      [BOARD]: board({
        items: [player(1, "polyterrasse", nobody), you(2, nobody)],
        viewer: you(2, nobody),
      }),
    });
    renderWithProviders(<LeaderboardScreen />);

    const line = await screen.findByText(
      "Nobody here has a resolved market yet. The podium is yours to take.",
    );
    expect(line.compareDocumentPosition(podium()!)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    expect(places().every((place) => place.hasAttribute("data-empty"))).toBe(true);
    expect(screen.queryByRole("img", { name: /place$/ })).not.toBeInTheDocument();
    expect(listed()).toEqual(["polyterrasse", "anna_m"]);
  });

  test("the line does not stand there while somebody has a figure", async () => {
    mockApi({ [BOARD]: board({}) });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("polyterrasse");

    expect(screen.queryByText(/The podium is yours to take/)).not.toBeInTheDocument();
  });

  test("a Lowest first list has no podium and no trophy: every row is listed", async () => {
    navigation.go("/leaderboard?order=asc");
    const api = mockApi({ [BOARD]: board({}) });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("polyterrasse");

    expect(sent(api.lastCall(BOARD))).toEqual({ sort: "credits", order: "asc" });
    expect(podium()).not.toBeInTheDocument();
    expect(screen.queryByRole("img", { name: /place$/ })).not.toBeInTheDocument();
    expect(listed()).toEqual(["polyterrasse", "cab_coffee", "anna_m", "mensa_fan"]);
    expect(screen.getByRole("button", { name: "Lowest first" })).toBeInTheDocument();
  });
});

describe("the rows are the API's", () => {
  test("they are shown in the order they arrive, never reordered", async () => {
    // An answer nobody would call sorted: the screen shows it as it is.
    mockApi({
      [BOARD]: board({
        items: [
          player(6, "sixth", { credits_cr: "5" }),
          player(4, "fourth", { credits_cr: "900" }),
          player(9, "ninth", { credits_cr: "12000" }),
          player(5, "fifth", { credits_cr: "0" }),
        ],
        viewer: null,
      }),
    });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("sixth");

    expect(listed()).toEqual(["sixth", "fourth", "ninth", "fifth"]);
  });

  test("an amount is cut and grouped as a string: no number could hold this one", async () => {
    mockApi({
      [BOARD]: board({
        items: [player(4, "whale", { credits_cr: "123456789012345678901234.999999" })],
        viewer: null,
      }),
    });
    renderWithProviders(<LeaderboardScreen />);

    await screen.findByText("whale");
    const row = rowOf("whale");
    expect(row).toHaveTextContent("123,456,789,012,345,678,901,234.99");
    expect(row).not.toHaveTextContent("235");
    expect(row).not.toHaveTextContent("e+");
  });

  test("nobody's real name or address appears, only the username and the programme", async () => {
    mockApi({ [BOARD]: board({}) });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("polyterrasse");

    expect(screen.queryByText(/@ethz\.ch/)).not.toBeInTheDocument();
    expect(screen.queryByText("Anna Muster")).not.toBeInTheDocument();
  });

  test("the figure of each measure is formatted as the API sent it", async () => {
    const figures = { credits_cr: "1204.5", win_rate: 0.625, trades: 1204, avg_payout_ratio: 1.35 };
    mockApi({ [BOARD]: board({ items: [player(4, "mensa_fan", figures)], viewer: null }) });
    const { user } = renderWithProviders(<LeaderboardScreen />);
    const row = () => rowOf("mensa_fan");

    await screen.findByText("mensa_fan");
    expect(row()).toHaveTextContent("1,204.5 tokens");

    await user.click(chip("Win rate"));
    await waitFor(() => expect(row()).toHaveTextContent("62.5%"));

    await user.click(chip("Trades"));
    await waitFor(() => expect(row()).toHaveTextContent("1,204"));
    expect(row()).not.toHaveTextContent("tokens");

    await user.click(chip("Payout"));
    await waitFor(() => expect(row()).toHaveTextContent(`1.35${TIMES}`));
  });
});

describe("sort and order", () => {
  test("a chip asks the API for its measure and puts it in the address", async () => {
    const api = mockApi({ [BOARD]: board({}) });
    const { user } = renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("polyterrasse");

    await user.click(chip("Win rate"));

    // One step in the browser's own history: the router follows without a round trip.
    expect(window.history.pushState).toHaveBeenCalledTimes(1);
    expect(window.history.pushState).toHaveBeenCalledWith(null, "", "/leaderboard?sort=win_rate");
    expect(navigation.href).toBe("/leaderboard?sort=win_rate");
    await waitFor(() =>
      expect(sent(api.lastCall(BOARD))).toEqual({ sort: "win_rate", order: "desc" }),
    );
    expect(chip("Win rate")).toHaveAttribute("aria-pressed", "true");
    expect(chip("Tokens")).toHaveAttribute("aria-pressed", "false");
    expect(
      screen.getByText("Share of resolved markets that returned more than was put in."),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Tokens in the wallet plus the value of open positions."),
    ).not.toBeInTheDocument();
  });

  test.each([
    ["Trades", "trades", "Number of trades made."],
    ["Payout", "avg_payout_ratio", "Tokens returned per token put in, over resolved markets."],
  ])("%s sorts by %s and states its measure", async (name, sort, caption) => {
    const api = mockApi({ [BOARD]: board({}) });
    const { user } = renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("polyterrasse");

    await user.click(chip(name));

    await waitFor(() => expect(sent(api.lastCall(BOARD))).toEqual({ sort, order: "desc" }));
    expect(navigation.search).toBe(`sort=${sort}`);
    expect(screen.getByText(caption)).toBeInTheDocument();
  });

  test("while another measure loads, the skeleton stands in: rows of one order are never shown under another", async () => {
    const slow = deferred();
    mockApi({
      [BOARD]: (request) => (request.query.get("sort") === "trades" ? slow.handler() : board({})),
    });
    const { user } = renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("polyterrasse");

    await user.click(chip("Trades"));

    expect(screen.queryByText("polyterrasse")).not.toBeInTheDocument();
    expect(document.querySelectorAll("[data-slot=row-skeletons] > li")).toHaveLength(5);

    slow.resolve(board({ items: [player(1, "busy_bee", { trades: 99 })], viewer: null }));
    expect(await screen.findByText("busy_bee")).toBeInTheDocument();
  });

  test("the order button flips the order, is named after the one that is active, and keeps the measure", async () => {
    navigation.go("/leaderboard?sort=trades");
    const api = mockApi({ [BOARD]: board({}) });
    const { user } = renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("polyterrasse");

    await user.click(screen.getByRole("button", { name: "Highest first" }));

    expect(navigation.href).toBe("/leaderboard?sort=trades&order=asc");
    await waitFor(() =>
      expect(sent(api.lastCall(BOARD))).toEqual({ sort: "trades", order: "asc" }),
    );
    expect(screen.getByRole("button", { name: "Lowest first" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Highest first" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Lowest first" }));

    expect(navigation.href).toBe("/leaderboard?sort=trades");
    expect(screen.getByRole("button", { name: "Highest first" })).toBeInTheDocument();
  });

  test("each change is a step in the history, so the back button returns to the view before it", async () => {
    mockApi({ [BOARD]: board({}) });
    const { user } = renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("polyterrasse");

    await user.click(chip("Trades"));
    await user.click(screen.getByRole("button", { name: "Highest first" }));
    expect(navigation.depth).toBe(2);

    act(() => navigation.router.back());

    expect(navigation.href).toBe("/leaderboard?sort=trades");
    await waitFor(() => expect(chip("Trades")).toHaveAttribute("aria-pressed", "true"));
    expect(screen.getByRole("button", { name: "Highest first" })).toBeInTheDocument();
  });

  test("tapping the chip that is pressed asks for nothing", async () => {
    const api = mockApi({ [BOARD]: board({}) });
    const { user } = renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("polyterrasse");

    await user.click(chip("Tokens"));

    expect(navigation.router.push).not.toHaveBeenCalled();
    expect(api.calls(BOARD)).toHaveLength(1);
  });
});

describe("the address is the view", () => {
  test("sort, order and every filter of the address are the query of the request", async () => {
    navigation.go(
      "/leaderboard?sort=avg_payout_ratio&order=asc&degree_level=MASTER" +
        "&degree_programme=MSc+Data+Science&min_credits_cr=0100.50&max_credits_cr=612.5" +
        "&min_win_rate=0.5&max_win_rate=1&min_trades=3&max_trades=40" +
        "&min_avg_payout_ratio=0.8&max_avg_payout_ratio=2.5",
    );
    const api = mockApi({ [BOARD]: board({}) });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("polyterrasse");

    expect(sent(api.lastCall(BOARD))).toEqual({
      sort: "avg_payout_ratio",
      order: "asc",
      degree_level: "MASTER",
      degree_programme: "MSc Data Science",
      // As it stands in the address: an amount is not read as a number on its way through.
      min_credits_cr: "0100.50",
      max_credits_cr: "612.5",
      min_win_rate: "0.5",
      max_win_rate: "1",
      min_trades: "3",
      max_trades: "40",
      min_avg_payout_ratio: "0.8",
      max_avg_payout_ratio: "2.5",
    });
    expect(chip("Payout")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Lowest first" })).toBeInTheDocument();
  });

  test("a sort or an order there is no control for opens the list as it opens by itself", async () => {
    navigation.go("/leaderboard?sort=score&order=sideways");
    const api = mockApi({ [BOARD]: board({}) });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("polyterrasse");

    expect(sent(api.lastCall(BOARD))).toEqual({ sort: "credits", order: "desc" });
    expect(chip("Tokens")).toHaveAttribute("aria-pressed", "true");
  });

  test("the filters that are set stand under the sort chips as chips, and Filter counts them", async () => {
    navigation.go(
      "/leaderboard?degree_level=BACHELOR&degree_programme=BSc+Computer+Science" +
        "&min_win_rate=0.5&max_credits_cr=1204.5&min_avg_payout_ratio=1.5",
    );
    mockApi({ [BOARD]: board({}) });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("polyterrasse");

    const chips = within(screen.getByRole("list", { name: "Filters that are set" })).getAllByRole(
      "button",
    );
    expect(chips.map((button) => button.textContent)).toEqual([
      "BSc Computer Science",
      "Tokens to 1,204.5",
      "Win rate from 50%",
      `Payout from 1.5${TIMES}`,
    ]);
    expect(chips[0]).toHaveAccessibleName("Remove filter: BSc Computer Science");
    // The number on the button is the number of chips under it.
    const filter = screen.getByRole("button", { name: "Filter, 4 set" });
    expect(filter.querySelector("[data-slot=badge]")).toHaveTextContent("4");
    // They wrap onto as many lines as they need, and none is wider than the column.
    const list = screen.getByRole("list", { name: "Filters that are set" });
    expect(list).toHaveClass("flex-wrap");
    for (const item of within(list).getAllByRole("listitem")) {
      expect(item).toHaveClass("max-w-full", "min-w-0");
    }
  });

  test("a level without a programme is shown by its name from /config", async () => {
    navigation.go("/leaderboard?degree_level=MASTER");
    mockApi({ [BOARD]: board({}) });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("polyterrasse");

    expect(screen.getByRole("button", { name: "Remove filter: Master" })).toBeInTheDocument();
    expect(screen.queryByText("MASTER")).not.toBeInTheDocument();
  });

  test("a chip removes its filter: from the address and from the next request", async () => {
    navigation.go("/leaderboard?sort=trades&degree_level=MASTER&min_win_rate=0.5&max_trades=40");
    const api = mockApi({ [BOARD]: board({}) });
    const { user } = renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("polyterrasse");

    await user.click(screen.getByRole("button", { name: "Remove filter: Win rate from 50%" }));

    expect(navigation.href).toBe("/leaderboard?sort=trades&degree_level=MASTER&max_trades=40");
    await waitFor(() =>
      expect(sent(api.lastCall(BOARD))).toEqual({
        sort: "trades",
        order: "desc",
        degree_level: "MASTER",
        max_trades: "40",
      }),
    );
    expect(
      screen.queryByRole("button", { name: "Remove filter: Win rate from 50%" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Filter, 2 set" })).toBeInTheDocument();
  });

  test("the chip of a degree removes the level and the programme together", async () => {
    navigation.go(
      "/leaderboard?degree_level=MASTER&degree_programme=MSc+Data+Science&min_trades=3",
    );
    const api = mockApi({ [BOARD]: board({}) });
    const { user } = renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("polyterrasse");

    await user.click(screen.getByRole("button", { name: "Remove filter: MSc Data Science" }));

    expect(navigation.href).toBe("/leaderboard?min_trades=3");
    await waitFor(() =>
      expect(sent(api.lastCall(BOARD))).toEqual({
        sort: "credits",
        order: "desc",
        min_trades: "3",
      }),
    );
  });
});

describe("your row", () => {
  test("the row the API marks is_viewer carries the label You; on the podium it stands under the username", async () => {
    mockApi({ [BOARD]: board({}) });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("polyterrasse");

    const [first, , third] = places();
    expect(third).toHaveTextContent("anna_m");
    const label = within(third!).getByText("You");
    expect(label.previousElementSibling).toHaveTextContent("anna_m");
    expect(within(first!).queryByText("You")).not.toBeInTheDocument();
  });

  test("in the rows it has a --raised background and the label You", async () => {
    mockApi({
      [BOARD]: board({
        items: [player(1, "a"), player(2, "b"), player(3, "c"), player(4, "d"), you(5)],
        viewer: you(5),
      }),
    });
    renderWithProviders(<LeaderboardScreen />);

    await screen.findByText("anna_m");
    const row = rowOf("anna_m");
    expect(row).toHaveClass("bg-raised");
    // "You" stands beside the username and is not cut with it.
    const label = within(row).getByText("You");
    expect(label).toHaveClass("shrink-0");
    expect(label.previousElementSibling).toHaveTextContent("anna_m");
    expect(rowOf("d")).not.toHaveClass("bg-raised");
  });

  test("it is marked by is_viewer, not by comparing names", async () => {
    // The API says nobody on this page is the viewer; a row with the viewer's username
    // is then just a row.
    mockApi({
      [BOARD]: board({
        items: [player(1, "a"), player(2, "b"), player(3, "c"), player(4, "anna_m")],
        viewer: null,
      }),
    });
    renderWithProviders(<LeaderboardScreen />);

    await screen.findByText("anna_m");
    expect(rowOf("anna_m")).not.toHaveClass("bg-raised");
    expect(screen.queryByText("You")).not.toBeInTheDocument();
    expect(pinnedBar()).not.toBeInTheDocument();
  });

  test("outside the page, the viewer row of the answer is pinned in a bar across the window, on the tab bar", async () => {
    mockApi({
      [BOARD]: board({
        items: [player(1, "a"), player(2, "b"), player(3, "c"), player(4, "d")],
        next_cursor: "4",
        viewer: you(1204, { credits_cr: "12.5" }),
      }),
    });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("a");

    const bar = pinnedBar()!;
    // Across the whole window, on the tab bar; --card with a line on top and 8 px above
    // and below the row.
    expect(bar).toHaveClass("fixed", "inset-x-0", "bottom-(--tab-bar-space)");
    expect(bar).toHaveClass("bg-card", "border-t", "py-2");
    const pinned = within(bar);
    // The row itself stands in the content column.
    expect(pinned.getByRole("region", { name: "Your rank" })).toHaveClass("mx-auto", "px-4");
    expect(bar).toHaveTextContent("Rank 1,204");
    expect(pinned.getByText("anna_m")).toBeInTheDocument();
    expect(pinned.getByText("You")).toBeInTheDocument();
    expect(bar).toHaveTextContent("12.5 tokens");
    const row = bar.querySelector("[data-slot=row]")!;
    expect(row).toHaveClass("bg-raised", "rounded-md");
    // The programme keeps to one line there.
    expect(pinned.getByText("BSc Computer Science")).toHaveClass("truncate");
    // Rank 1,204 is no podium place: no trophy.
    expect(trophy(bar)).not.toBeInTheDocument();
  });

  test("a list the viewer is in keeps room at its foot for the bar, whether or not the bar shows", async () => {
    vi.stubGlobal("IntersectionObserver", Watcher);
    mockApi({
      [BOARD]: board({
        items: [player(1, "a"), player(2, "b"), player(3, "c"), player(4, "d"), you(5)],
        viewer: you(5),
      }),
    });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("anna_m");
    // The bar is 73 px tall, and the last row ends 16 px above it.
    const room = "pb-[calc(var(--tab-bar-space)+5.5rem+1px)]";

    // The row is in sight and nothing is pinned: the room is there all the same, so the
    // page does not grow in the moment the bar comes.
    expect(pinnedBar()).not.toBeInTheDocument();
    expect(main()).toHaveClass(room);

    report(viewerRow()!, 0);
    expect(pinnedBar()).toBeInTheDocument();
    expect(main()).toHaveClass(room);

    report(viewerRow()!, 1);
    expect(pinnedBar()).not.toBeInTheDocument();
    expect(main()).toHaveClass(room);
  });

  test("a list the viewer is not in has no bar and keeps no room for one", async () => {
    navigation.go("/leaderboard?degree_level=DOCTORATE");
    mockApi({ [BOARD]: board({ items: [player(1, "a"), player(2, "b")], viewer: null }) });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("a");

    expect(pinnedBar()).not.toBeInTheDocument();
    expect(main().className).not.toMatch(/5\.5rem/);
    expect(main()).toHaveClass("pb-[calc(var(--tab-bar-space)+1.5rem)]");
  });

  test("the pinned row shows the figure of the measure the list is sorted by", async () => {
    navigation.go("/leaderboard?sort=win_rate");
    mockApi({
      [BOARD]: board({
        items: [player(1, "a"), player(2, "b"), player(3, "c")],
        viewer: you(57, { win_rate: 0.4 }),
      }),
    });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("a");

    expect(pinnedBar()).toHaveTextContent("40.0%");
    expect(pinnedBar()).not.toHaveTextContent("tokens");
  });

  test("when viewer is null the filters leave the viewer out, and nothing is pinned", async () => {
    navigation.go("/leaderboard?degree_level=DOCTORATE");
    mockApi({ [BOARD]: board({ items: [player(1, "a"), player(2, "b")], viewer: null }) });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("a");

    expect(pinnedBar()).not.toBeInTheDocument();
  });

  test("a row that is in the list is pinned while it is scrolled away, and not while it is on screen", async () => {
    vi.stubGlobal("IntersectionObserver", Watcher);
    mockApi({
      [BOARD]: board({
        items: [player(1, "a"), player(2, "b"), player(3, "c"), player(4, "d"), you(5)],
        viewer: you(5),
      }),
    });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("anna_m");

    // Until the browser has said where the row is, nothing is pinned: no flash on opening.
    expect(pinnedBar()).not.toBeInTheDocument();
    expect(watching(viewerRow()!)).toHaveLength(1);

    report(viewerRow()!, 0);
    expect(within(pinnedBar()!).getByText("anna_m")).toBeInTheDocument();
    expect(pinnedBar()).toHaveTextContent("Rank 5");

    report(viewerRow()!, 1);
    expect(pinnedBar()).not.toBeInTheDocument();

    // A sliver under a bar is not "on screen": at least half of the row has to show.
    report(viewerRow()!, 0.2);
    expect(pinnedBar()).toBeInTheDocument();
    report(viewerRow()!, 0.5);
    expect(pinnedBar()).not.toBeInTheDocument();
  });

  test("on screen means between the bars of the shell, whose room the browser resolves", async () => {
    vi.stubGlobal("IntersectionObserver", Watcher);
    const computed = window.getComputedStyle.bind(window);
    vi.spyOn(window, "getComputedStyle").mockImplementation((element, pseudo) =>
      // The one element that carries the two lengths of the shell as its padding.
      element.classList.contains("pt-(--top-bar-space)")
        ? ({ paddingTop: "59.5px", paddingBottom: "98px" } as CSSStyleDeclaration)
        : computed(element, pseudo),
    );
    mockApi({
      [BOARD]: board({
        items: [player(1, "a"), player(2, "b"), player(3, "c"), you(4)],
        viewer: you(4),
      }),
    });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("anna_m");

    expect(watching(viewerRow()!)[0]?.options).toEqual({
      rootMargin: "-59.5px 0px -98px 0px",
      threshold: 0.5,
    });
  });

  test("a podium row that is scrolled away is pinned with its 24 px trophy beside the username", async () => {
    vi.stubGlobal("IntersectionObserver", Watcher);
    mockApi({ [BOARD]: board({}) });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("polyterrasse");
    expect(viewerRow()).toBe(places()[2]);

    report(viewerRow()!, 0);

    const bar = pinnedBar()!;
    expect(within(bar).getByRole("img", { name: "Third place" })).toHaveClass("size-6");
    expect(bar).toHaveTextContent("Rank 3");
    expect(within(bar).getByText("You")).toBeInTheDocument();
  });

  test("a pinned row carries no trophy in a Lowest first list, nor without a figure", async () => {
    navigation.go("/leaderboard?order=asc");
    mockApi({
      [BOARD]: (request) =>
        request.query.get("sort") === "win_rate"
          ? board({
              items: [player(1, "a"), player(3, "c"), player(4, "d")],
              viewer: you(2, { win_rate: null, avg_payout_ratio: null }),
            })
          : board({ items: [player(2, "b"), player(3, "c"), player(4, "d")], viewer: you(1) }),
    });
    const { user } = renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("b");

    // First of a Lowest first list: a trophy is not for last place.
    expect(pinnedBar()).toHaveTextContent("Rank 1");
    expect(trophy(pinnedBar()!)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Lowest first" }));
    await user.click(chip("Win rate"));
    await screen.findByText("a");

    // Second by rank, but without a rate: not one of the three podium rows.
    expect(pinnedBar()).toHaveTextContent("Rank 2");
    expect(within(pinnedBar()!).getByText("none yet")).toBeInTheDocument();
    expect(trophy(pinnedBar()!)).not.toBeInTheDocument();
  });

  test("a row that arrives with a later page takes over from the pinned one once it is on screen", async () => {
    vi.stubGlobal("IntersectionObserver", Watcher);
    mockApi({
      [BOARD]: (request) =>
        request.query.has("cursor")
          ? board({ items: [player(5, "e"), you(6)], next_cursor: null, viewer: you(6) })
          : board({
              items: [player(1, "a"), player(2, "b"), player(3, "c"), player(4, "d")],
              next_cursor: "4",
              viewer: you(6),
            }),
    });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("d");
    expect(pinnedBar()).toHaveTextContent("Rank 6");

    report(document.querySelector("[data-slot=load-more]")!, 1);
    await screen.findByText("e");

    // The row is loaded now, but the browser has not said that it shows: still pinned.
    expect(viewerRow()).toBeInTheDocument();
    expect(pinnedBar()).toBeInTheDocument();

    report(viewerRow()!, 1);
    expect(pinnedBar()).not.toBeInTheDocument();
  });
});

describe("paging", () => {
  const firstPage = () =>
    board({
      items: [player(1, "a"), player(2, "b"), player(3, "c"), player(4, "d")],
      next_cursor: "4",
      viewer: you(9),
    });
  const lastPage = () =>
    board({ items: [player(5, "e"), player(6, "f")], next_cursor: null, viewer: you(8) });

  test("more rows load with next_cursor under the same sort and filters, without a page size", async () => {
    navigation.go("/leaderboard?sort=trades&order=asc&degree_level=MASTER");
    const api = mockApi({
      [BOARD]: (request) => (request.query.has("cursor") ? lastPage() : firstPage()),
    });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("d");
    expect(api.calls(BOARD)).toHaveLength(1);
    expect(screen.queryByText("e")).not.toBeInTheDocument();

    act(() => intersect(document.querySelector("[data-slot=load-more]")!));

    expect(await screen.findByText("f")).toBeInTheDocument();
    expect(sent(api.lastCall(BOARD))).toEqual({
      sort: "trades",
      order: "asc",
      degree_level: "MASTER",
      cursor: "4",
    });
    expect(listed()).toEqual(["a", "b", "c", "d", "e", "f"]);

    // The last page is there: nothing more is asked for.
    await waitFor(() =>
      expect(document.querySelector("[data-slot=load-more]")).not.toBeInTheDocument(),
    );
    expect(api.calls(BOARD)).toHaveLength(2);
  });

  test("the pinned row is the viewer of the page read last", async () => {
    mockApi({
      [BOARD]: (request) => (request.query.has("cursor") ? lastPage() : firstPage()),
    });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("d");
    expect(pinnedBar()).toHaveTextContent("Rank 9");

    act(() => intersect(document.querySelector("[data-slot=load-more]")!));

    await screen.findByText("f");
    expect(pinnedBar()).toHaveTextContent("Rank 8");
  });

  test("a player who shows on two pages is shown twice: the screen shows what it gets", async () => {
    mockApi({
      [BOARD]: (request) =>
        request.query.has("cursor")
          ? board({ items: [player(5, "d"), player(6, "e")], next_cursor: null, viewer: null })
          : board({
              items: [player(1, "a"), player(2, "b"), player(3, "c"), player(4, "d")],
              next_cursor: "4",
              viewer: null,
            }),
    });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("d");

    act(() => intersect(document.querySelector("[data-slot=load-more]")!));

    await screen.findByText("e");
    expect(listed()).toEqual(["d", "d", "e"]);
  });

  test("a page that failed keeps the rows that are there and asks again only on tap", async () => {
    const api = mockApi({
      [BOARD]: (request) => (request.query.has("cursor") ? UNAVAILABLE : firstPage()),
    });
    const { user } = renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("d");

    act(() => intersect(document.querySelector("[data-slot=load-more]")!));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This is temporarily unavailable. Try again shortly.",
    );
    expect(listed()).toEqual(["d"]);
    expect(podium()).toBeInTheDocument();

    api.on(BOARD, lastPage());
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByText("f")).toBeInTheDocument();
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
          win_rate: 0.625,
          trades: 1204,
          avg_payout_ratio: 1.35,
          degree_programme: "MSc Data Science",
        }),
        you(5, { win_rate: null, avg_payout_ratio: null }),
      ],
      viewer: you(5, { win_rate: null, avg_payout_ratio: null }),
    });

  test("the rows below the podium are a table with all four measures as columns", async () => {
    mockApi({ [BOARD]: answer() });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("mensa_fan");

    const table = screen.getByRole("table", { name: "Ranking" });
    expect(
      within(table)
        .getAllByRole("columnheader")
        .map((heading) => heading.textContent),
    ).toEqual(["Rank", "Player", "Tokens", "Win rate", "Trades", "Payout"]);

    const [, row, yours] = within(table).getAllByRole("row");
    const cells = within(row!)
      .getAllByRole("cell")
      .map((cell) => cell.textContent);
    expect(cells[0]).toBe("4");
    expect(cells[1]).toBe("mensa_fanMSc Data Science");
    expect(cells[2]).toContain("120.25 tokens");
    expect(cells[3]).toBe("62.5%");
    expect(cells[4]).toBe("1,204");
    expect(cells[5]).toBe(`1.35${TIMES}`);

    // The podium stays a podium; its three rows are not repeated in the table.
    expect(podium()).toBeInTheDocument();
    expect(within(table).getAllByRole("row")).toHaveLength(3);

    expect(yours).toHaveClass("bg-raised", "viewer-row");
    expect(within(yours!).getByText("You")).toBeInTheDocument();
    expect(within(yours!).getAllByText("none yet")).toHaveLength(2);
  });

  test("the rows are 56 px tall; the player gives way with an ellipsis and the figures keep their width", async () => {
    mockApi({ [BOARD]: answer() });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("mensa_fan");

    const [head, row] = within(screen.getByRole("table")).getAllByRole("row");
    expect(row).toHaveClass("h-14");
    const [rank, who, ...figures] = within(row!).getAllByRole("cell");
    // "Rank" is 56 px wide.
    expect(within(head!).getByRole("columnheader", { name: "Rank" })).toHaveClass(
      "w-14",
      "min-w-14",
    );
    expect(rank).toHaveClass("text-right", "whitespace-nowrap");
    // "Player" takes what the others leave; the username and the programme are one line each.
    expect(who).toHaveClass("w-full", "max-w-0");
    expect(within(who!).getByText("mensa_fan")).toHaveClass("truncate");
    expect(within(who!).getByText("MSc Data Science")).toHaveClass("truncate");
    for (const figure of figures) {
      expect(figure).toHaveClass("text-right", "whitespace-nowrap", "w-px", "px-2");
    }
  });

  test("the sort chips are as wide as their labels, and the first-place figure is in number-lg", async () => {
    mockApi({ [BOARD]: answer() });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("mensa_fan");

    expect(sortChips()).toHaveAttribute("data-layout", "strip");
    const [first, second] = places();
    expect(first!.querySelector("[data-slot=amount]")).toHaveClass("type-number-lg");
    expect(second!.querySelector("[data-slot=amount]")).toHaveClass("type-number");
  });

  test("the heading of the sorted measure carries aria-sort; the others say none", async () => {
    mockApi({ [BOARD]: answer() });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("mensa_fan");

    const sorts = Object.fromEntries(
      screen
        .getAllByRole("columnheader")
        .map((heading) => [heading.textContent, heading.getAttribute("aria-sort")]),
    );
    expect(sorts).toEqual({
      Rank: null,
      Player: null,
      Tokens: "descending",
      "Win rate": "none",
      Trades: "none",
      Payout: "none",
    });
  });

  test("a tap on a column heading sorts by it; a second tap flips the order", async () => {
    const api = mockApi({ [BOARD]: answer() });
    const { user } = renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("mensa_fan");
    const heading = (name: string) => screen.getByRole("columnheader", { name });

    await user.click(within(heading("Trades")).getByRole("button"));

    expect(navigation.href).toBe("/leaderboard?sort=trades");
    await waitFor(() =>
      expect(sent(api.lastCall(BOARD))).toEqual({ sort: "trades", order: "desc" }),
    );
    await screen.findByText("mensa_fan");
    expect(heading("Trades")).toHaveAttribute("aria-sort", "descending");
    expect(heading("Tokens")).toHaveAttribute("aria-sort", "none");
    // The sort chips say the same.
    expect(chip("Trades")).toHaveAttribute("aria-pressed", "true");

    await user.click(within(heading("Trades")).getByRole("button"));

    expect(navigation.href).toBe("/leaderboard?sort=trades&order=asc");
    await waitFor(() =>
      expect(sent(api.lastCall(BOARD))).toEqual({ sort: "trades", order: "asc" }),
    );
    await waitFor(() => expect(heading("Trades")).toHaveAttribute("aria-sort", "ascending"));
    // Lowest first: no podium, and its three rows are in the table.
    expect(podium()).not.toBeInTheDocument();
    expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(6);
  });

  test("a table row that is scrolled away is pinned as a row, at the lower edge of the window", async () => {
    vi.stubGlobal("IntersectionObserver", Watcher);
    mockApi({ [BOARD]: answer() });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("mensa_fan");
    expect(viewerRow()?.tagName).toBe("TR");

    report(viewerRow()!, 0);

    expect(pinnedBar()).toHaveTextContent("Rank 5");
    expect(within(pinnedBar()!).getByText("anna_m")).toBeInTheDocument();
    // No tab bar lies under it there: the bar keeps clear of the safe area itself.
    expect(pinnedBar()).toHaveClass("md:pb-[calc(0.5rem+env(safe-area-inset-bottom))]");
    expect(within(pinnedBar()!).getByRole("region", { name: "Your rank" })).toHaveClass(
      "md:max-w-[45rem]",
    );
  });
});

describe("the bean drop", () => {
  const beans = () => document.querySelectorAll("[data-slot=bean-drop] .animate-bean-drop");
  const first = () =>
    board({
      items: [you(1, { credits_cr: "5000" }), player(2, "b"), player(3, "c"), player(4, "d")],
      viewer: you(1, { credits_cr: "5000" }),
    });

  beforeEach(() => setMediaQuery(REDUCED_MOTION_QUERY, false));

  test("plays once on the first-place figure when the viewer opens the unfiltered Tokens list in first place", async () => {
    mockApi({ [BOARD]: first() });
    const { user } = renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("anna_m");

    expect(beans().length).toBeGreaterThanOrEqual(3);
    expect(beans().length).toBeLessThanOrEqual(5);
    expect(beans()[0]!.closest("[data-slot=podium-place]")).toBe(places()[0]);
    // The figure is there from the start: nothing counts up.
    expect(places()[0]).toHaveTextContent("5,000 tokens");

    await waitFor(() => expect(beans()).toHaveLength(0), { timeout: 3000 });

    // Coming back to the list is not opening it in first place a second time.
    await user.click(chip("Trades"));
    await waitFor(() => expect(chip("Trades")).toHaveAttribute("aria-pressed", "true"));
    await user.click(chip("Tokens"));
    await screen.findByText("anna_m");
    expect(beans()).toHaveLength(0);
  });

  test.each([
    ["another measure", "/leaderboard?sort=trades"],
    ["a filtered list", "/leaderboard?degree_level=BACHELOR"],
    ["a Lowest first list", "/leaderboard?order=asc"],
  ])("does not play for %s", async (_name, address) => {
    navigation.go(address);
    mockApi({ [BOARD]: first() });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("anna_m");

    expect(beans()).toHaveLength(0);
  });

  test("does not play for anybody who is not first", async () => {
    mockApi({ [BOARD]: board({}) });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("polyterrasse");

    expect(beans()).toHaveLength(0);
  });

  test("with reduced motion nothing falls", async () => {
    setMediaQuery(REDUCED_MOTION_QUERY, true);
    mockApi({ [BOARD]: first() });
    renderWithProviders(<LeaderboardScreen />);
    await screen.findByText("anna_m");

    expect(beans()).toHaveLength(0);
    expect(places()[0]).toHaveTextContent("5,000 tokens");
  });
});
