import { describe, expect, test } from "vitest";

import { TIMES } from "@/lib/format";
import { config, leaderboardEntry } from "@/test/fixtures";

import {
  arrange,
  cleanBound,
  draftOf,
  type Filters,
  filtersOf,
  finishDraft,
  hasFigure,
  isPodiumRow,
  MEASURES,
  measureOf,
  OPENING_VIEW,
  queryOf,
  readView,
  requestOf,
  setFilters,
  type View,
  viewerPlacement,
  withoutFilters,
} from "./view";

const read = (query: string) => readView(new URLSearchParams(query));
const degrees = config().degrees;

describe("the four measures", () => {
  test("carry the chip names and the captions of DESIGN.md §13, in the order of the sort row", () => {
    expect(MEASURES.map((measure) => [measure.sort, measure.name, measure.caption])).toEqual([
      ["credits", "Tokens", "Tokens held right now."],
      ["win_rate", "Win rate", "Share of resolved markets that returned more than was put in."],
      ["trades", "Trades", "Number of trades made."],
      ["avg_payout_ratio", "Payout", "Tokens returned per token put in, over resolved markets."],
    ]);
  });

  test("each is filtered under the names the API takes", () => {
    expect(MEASURES.map((measure) => [measure.from, measure.to])).toEqual([
      ["min_credits_cr", "max_credits_cr"],
      ["min_win_rate", "max_win_rate"],
      ["min_trades", "max_trades"],
      ["min_avg_payout_ratio", "max_avg_payout_ratio"],
    ]);
    expect(measureOf("trades").name).toBe("Trades");
  });
});

describe("the address", () => {
  test("a plain address is the list as it opens: Tokens, Highest first, no filters", () => {
    expect(read("")).toEqual({ sort: "credits", order: "desc", filters: {} });
    expect(read("")).toEqual(OPENING_VIEW);
    expect(queryOf(OPENING_VIEW)).toBe("");
  });

  test("sort, order and every filter are read under the API's names", () => {
    expect(
      read(
        "sort=win_rate&order=asc&degree_level=MASTER&degree_programme=MSc+Data+Science" +
          "&min_credits_cr=100&max_credits_cr=612.5&min_win_rate=0.5&max_win_rate=1" +
          "&min_trades=3&max_trades=40&min_avg_payout_ratio=0.8&max_avg_payout_ratio=2.5",
      ),
    ).toEqual({
      sort: "win_rate",
      order: "asc",
      filters: {
        degree_level: "MASTER",
        degree_programme: "MSc Data Science",
        min_credits_cr: "100",
        max_credits_cr: "612.5",
        min_win_rate: "0.5",
        max_win_rate: "1",
        min_trades: "3",
        max_trades: "40",
        min_avg_payout_ratio: "0.8",
        max_avg_payout_ratio: "2.5",
      },
    });
  });

  test("a sort or an order there is no control for falls back to the opening one", () => {
    expect(read("sort=score&order=down")).toEqual(OPENING_VIEW);
    expect(read("sort=trades&order=DESC").order).toBe("desc");
  });

  test("a filter is taken as it stands, whatever it says: the API judges it", () => {
    expect(read("min_trades=abc&max_win_rate=66.7&degree_level=KINDERGARTEN").filters).toEqual({
      degree_level: "KINDERGARTEN",
      max_win_rate: "66.7",
      min_trades: "abc",
    });
  });

  test("an empty value and a parameter nobody defined are not filters", () => {
    expect(read("min_trades=&limit=200&cursor=40&total=true").filters).toEqual({});
  });

  test("a view and its address are the same thing, both ways", () => {
    const view: View = {
      sort: "avg_payout_ratio",
      order: "asc",
      filters: { degree_level: "BACHELOR", degree_programme: "BSc Physics", min_win_rate: "0.5" },
    };

    expect(queryOf(view)).toBe(
      "sort=avg_payout_ratio&order=asc&degree_level=BACHELOR&degree_programme=BSc+Physics&min_win_rate=0.5",
    );
    expect(read(queryOf(view))).toEqual(view);
  });

  test("what the list opens on is left out of the address", () => {
    expect(queryOf({ sort: "credits", order: "asc", filters: {} })).toBe("order=asc");
    expect(queryOf({ sort: "trades", order: "desc", filters: {} })).toBe("sort=trades");
  });
});

describe("the request", () => {
  test("always names the sort and the order, and never a page size", () => {
    expect(requestOf(OPENING_VIEW)).toEqual({ sort: "credits", order: "desc" });
    expect(Object.keys(requestOf(read("sort=trades&min_trades=3")))).toEqual([
      "sort",
      "order",
      "min_trades",
    ]);
  });

  test("every filter goes out as the text the address holds: nothing becomes a number", () => {
    const request = requestOf(
      read("min_credits_cr=0100.50&max_credits_cr=12345678901234567890.123456&min_trades=007"),
    );

    expect(request.min_credits_cr).toBe("0100.50");
    expect(request.max_credits_cr).toBe("12345678901234567890.123456");
    expect(request.min_trades).toBe("007");
    for (const value of Object.values(request)) expect(typeof value).toBe("string");
  });
});

describe("the fields of the filter sheet", () => {
  test("start empty, each filter under its name", () => {
    const draft = draftOf({});

    expect(Object.keys(draft)).toHaveLength(10);
    expect(new Set(Object.values(draft))).toEqual(new Set([""]));
  });

  test("a win rate is typed in percent and sent as a fraction, by moving the point in the string", () => {
    const draft = { ...draftOf({}), min_win_rate: "62.5", max_win_rate: "100" };

    expect(filtersOf(draft)).toEqual({ min_win_rate: "0.625", max_win_rate: "1" });
    expect(draftOf({ min_win_rate: "0.625", max_win_rate: "1" })).toMatchObject({
      min_win_rate: "62.5",
      max_win_rate: "100",
    });
  });

  test("the other fields are sent exactly as typed", () => {
    const draft = {
      ...draftOf({}),
      degree_level: "MASTER",
      degree_programme: "MSc Data Science",
      min_credits_cr: "0100.50",
      max_credits_cr: "12345678901234567890.123456",
      min_trades: "007",
      max_avg_payout_ratio: "1.50",
    };

    expect(filtersOf(draft)).toEqual({
      degree_level: "MASTER",
      degree_programme: "MSc Data Science",
      min_credits_cr: "0100.50",
      max_credits_cr: "12345678901234567890.123456",
      min_trades: "007",
      max_avg_payout_ratio: "1.50",
    });
  });

  test("a from above its to is not looked at: both go out", () => {
    const draft = { ...draftOf({}), min_credits_cr: "500", max_credits_cr: "100" };

    expect(filtersOf(draft)).toEqual({ min_credits_cr: "500", max_credits_cr: "100" });
  });

  test("what is no percentage goes out as it is, and comes back as it was", () => {
    expect(filtersOf({ ...draftOf({}), min_win_rate: "abc" })).toEqual({ min_win_rate: "abc" });
    expect(draftOf({ min_win_rate: "abc" }).min_win_rate).toBe("abc");
  });

  test("an empty field is no filter", () => {
    expect(filtersOf(draftOf({}))).toEqual({});
  });

  test("a field keeps the characters a figure can have, and nothing else", () => {
    expect(cleanBound("amount", "1,204.5 tokens")).toBe("1,204.5");
    expect(cleanBound("percent", "62,5%")).toBe("62,5");
    expect(cleanBound("ratio", "x1.5")).toBe("1.5");
    expect(cleanBound("amount", "-5")).toBe("5");
    expect(cleanBound("count", "1,204")).toBe("1204");
    expect(cleanBound("count", "abc")).toBe("");
  });

  const finished = (typed: string) =>
    finishDraft({ ...draftOf({}), min_credits_cr: typed }).min_credits_cr;

  test("a figure grouped the way the app writes it is read that way", () => {
    expect(finished("1,204.5")).toBe("1204.5");
    expect(finished("1,000")).toBe("1000");
    expect(finished("12,345,678.25")).toBe("12345678.25");
  });

  test("a decimal comma, as a phone's decimal key types it, becomes the point", () => {
    expect(finished("62,5")).toBe("62.5");
    expect(finished("0,125")).toBe("0.125");
    expect(finished("1000,5")).toBe("1000.5");
    expect(finished(",5")).toBe("0.5");
  });

  test("a separator nothing follows is dropped, and a leading one gets its zero", () => {
    expect(finished("5.")).toBe("5");
    expect(finished("5,")).toBe("5");
    expect(finished(".5")).toBe("0.5");
    expect(finished(" 100 ")).toBe("100");
  });

  test("a plain figure is left exactly as typed: no digit is added, dropped or rounded", () => {
    for (const typed of ["0100.50", "12345678901234567890.123456", "007", "1.50", "0"]) {
      expect(finished(typed)).toBe(typed);
    }
  });

  test("what cannot be read is not guessed at: it goes to the API as it is", () => {
    for (const typed of ["1.204,5", "1,2,3", "1.2.3", "12,34.5"]) {
      expect(finished(typed)).toBe(typed);
    }
  });

  test("only the bounds are figures: a programme with a comma in it stays as it is", () => {
    const draft = finishDraft({
      ...draftOf({}),
      degree_programme: "MSc Science, Technology and Policy",
      min_win_rate: "5.",
      max_win_rate: ",5",
    });

    expect(draft.degree_programme).toBe("MSc Science, Technology and Policy");
    expect(filtersOf(draft)).toMatchObject({ min_win_rate: "0.05", max_win_rate: "0.005" });
  });
});

describe("the chips of the filters that are set", () => {
  const labels = (filters: Filters) => setFilters(filters, degrees).map((chip) => chip.label);

  test("none while nothing is set", () => {
    expect(setFilters({}, degrees)).toEqual([]);
  });

  test("the degree is one chip: the programme as sent, or the level by its name from /config", () => {
    expect(labels({ degree_level: "MASTER" })).toEqual(["Master"]);
    expect(labels({ degree_level: "MASTER", degree_programme: "MSc Data Science" })).toEqual([
      "MSc Data Science",
    ]);
    expect(
      setFilters({ degree_level: "MASTER", degree_programme: "MSc Data Science" }, degrees)[0]
        ?.names,
    ).toEqual(["degree_level", "degree_programme"]);
  });

  test("each bound is a chip in the unit of its measure", () => {
    expect(
      labels({
        min_credits_cr: "1204.5",
        max_credits_cr: "5000",
        min_win_rate: "0.5",
        max_win_rate: "0.625",
        min_trades: "3",
        max_trades: "40",
        min_avg_payout_ratio: "0.8",
        max_avg_payout_ratio: "1.5",
      }),
    ).toEqual([
      "Tokens from 1,204.5",
      "Tokens to 5,000",
      "Win rate from 50%",
      "Win rate to 62.5%",
      "Trades from 3",
      "Trades to 40",
      `Payout from 0.8${TIMES}`,
      `Payout to 1.5${TIMES}`,
    ]);
  });

  test("an amount keeps every digit that was typed", () => {
    expect(labels({ min_credits_cr: "12345678901234567890.123456" })).toEqual([
      "Tokens from 12,345,678,901,234,567,890.123456",
    ]);
  });

  test("removing a chip removes what it names and nothing else", () => {
    const filters: Filters = {
      degree_level: "MASTER",
      degree_programme: "MSc Data Science",
      min_win_rate: "0.5",
      max_trades: "40",
    };

    expect(withoutFilters(filters, ["degree_level", "degree_programme"])).toEqual({
      min_win_rate: "0.5",
      max_trades: "40",
    });
    expect(withoutFilters(filters, ["min_win_rate"])).toEqual({
      degree_level: "MASTER",
      degree_programme: "MSc Data Science",
      max_trades: "40",
    });
  });
});

describe("the podium", () => {
  const first = leaderboardEntry({ rank: 1, username: "polyterrasse" });
  const second = leaderboardEntry({ rank: 2, username: "cab_coffee" });
  const third = leaderboardEntry({ rank: 3, username: "anna_m", is_viewer: true });
  const fourth = leaderboardEntry({ rank: 4, username: "mensa_fan" });
  const unrated = (rank: number, username: string) =>
    leaderboardEntry({ rank, username, win_rate: null, avg_payout_ratio: null });

  test("a rate can be missing; tokens and trades never are", () => {
    const entry = unrated(2, "fresh");

    expect(hasFigure(entry, "credits")).toBe(true);
    expect(hasFigure(entry, "trades")).toBe(true);
    expect(hasFigure(entry, "win_rate")).toBe(false);
    expect(hasFigure(entry, "avg_payout_ratio")).toBe(false);
    expect(hasFigure(first, "win_rate")).toBe(true);
  });

  test("the rows of rank 1, 2 and 3 of a Highest first list stand on it, the rest below", () => {
    const { podium, rows } = arrange([first, second, third, fourth], "credits", "desc");

    expect(podium).toEqual([first, second, third]);
    expect(rows).toEqual([fourth]);
  });

  test("a place is the one its rank names, whatever the order of the answer", () => {
    const { podium, rows } = arrange([third, fourth, first], "credits", "desc");

    expect(podium).toEqual([first, null, third]);
    expect(rows).toEqual([fourth]);
  });

  test("a place stays empty when the list has no row of that rank", () => {
    expect(arrange([first], "credits", "desc").podium).toEqual([first, null, null]);
    expect(arrange([], "credits", "desc")).toEqual({ podium: [null, null, null], rows: [] });
  });

  test("a row without a figure for the sorted measure leaves its place empty and is listed below", () => {
    const fresh = unrated(2, "fresh");

    const { podium, rows } = arrange([first, fresh, fourth], "win_rate", "desc");

    expect(podium).toEqual([first, null, null]);
    expect(rows).toEqual([fresh, fourth]);
    // The same row has a place when the measure is one it has a figure for.
    expect(arrange([first, fresh, fourth], "trades", "desc").podium).toEqual([first, fresh, null]);
  });

  test("a Lowest first list has no podium: every row is listed, in the order it arrived", () => {
    const answer = [first, second, third, fourth];

    expect(arrange(answer, "credits", "asc")).toEqual({ podium: null, rows: answer });
  });

  test("the rows below keep the order of the answer: nothing is sorted here", () => {
    const sixth = leaderboardEntry({ rank: 6, username: "f", credits_cr: "900" });
    const fifth = leaderboardEntry({ rank: 5, username: "e", credits_cr: "1" });

    expect(arrange([first, sixth, fourth, fifth], "credits", "desc").rows).toEqual([
      sixth,
      fourth,
      fifth,
    ]);
  });

  test("a row that shows on two pages is listed twice; only the first stands on the podium", () => {
    const again = { ...first };

    const { podium, rows } = arrange([first, second, again], "credits", "desc");

    expect(podium?.[0]).toBe(first);
    expect(rows).toEqual([again]);
  });

  test("a trophy is for a podium row of a Highest first list, and for nothing else", () => {
    expect(isPodiumRow(third, "credits", "desc")).toBe(true);
    expect(isPodiumRow(third, "credits", "asc")).toBe(false);
    expect(isPodiumRow(fourth, "credits", "desc")).toBe(false);
    expect(isPodiumRow(unrated(2, "fresh"), "win_rate", "desc")).toBe(false);
    expect(isPodiumRow(unrated(2, "fresh"), "trades", "desc")).toBe(true);
  });

  test("the viewer's row is found where it is drawn, by is_viewer alone", () => {
    expect(viewerPlacement(arrange([first, second, third, fourth], "credits", "desc"))).toBe(
      "podium:2",
    );
    expect(viewerPlacement(arrange([first, second, third, fourth], "credits", "asc"))).toBe(
      "row:2",
    );
    expect(viewerPlacement(arrange([first, second, fourth], "credits", "desc"))).toBeNull();
    const below = leaderboardEntry({ rank: 9, username: "anna_m", is_viewer: true });
    expect(viewerPlacement(arrange([first, second, fourth, below], "credits", "desc"))).toBe(
      "row:1",
    );
  });
});
