import type { LeaderboardParams } from "@/lib/api/keys";
import type {
  DegreeLevelOption,
  LeaderboardEntry,
  LeaderboardSort,
  SortOrder,
} from "@/lib/api/schema";
import { formatAmount, fractionToPercent, percentToFraction, TIMES } from "@/lib/format";

/**
 * What the leaderboard shows, as the address says it (DESIGN.md §13): the measure it is
 * sorted by, the direction, and the filters. The address holds all three under the names
 * `GET /leaderboard` takes, so a view can be shared and the back button works, and the
 * request is the address: the server sorts, filters and ranks, and nothing here does.
 *
 * Everything in this file is text. A filter is the string that stands in the address and
 * goes to the API as it stands; an amount is never turned into a number, and a "from" is
 * never compared with a "to".
 */

/** The bounds a list can be filtered by, each under the name the API takes it. */
export const BOUND_NAMES = [
  "min_credits_cr",
  "max_credits_cr",
  "min_win_rate",
  "max_win_rate",
  "min_trades",
  "max_trades",
  "min_avg_payout_ratio",
  "max_avg_payout_ratio",
] as const;

/** Every filter of `GET /leaderboard`, in the order the address lists them. */
export const FILTER_NAMES = ["degree_level", "degree_programme", ...BOUND_NAMES] as const;

export type BoundName = (typeof BOUND_NAMES)[number];
export type FilterName = (typeof FILTER_NAMES)[number];

/** The filters that are set, as the text the address holds. One that is not set is absent. */
export type Filters = Partial<Record<FilterName, string>>;

export type View = {
  sort: LeaderboardSort;
  order: SortOrder;
  filters: Filters;
};

/** How a bound is typed and shown: as an amount, a percentage, a whole number or a ratio. */
export type BoundKind = "amount" | "percent" | "count" | "ratio";

export type Measure = {
  /** The key the API sorts by. A key is not copy: the words are the three below. */
  sort: LeaderboardSort;
  /** The name on its chip, its column heading and its row of the filter sheet. */
  name: string;
  /** The line under the title while the list is sorted by it. */
  caption: string;
  from: BoundName;
  to: BoundName;
  kind: BoundKind;
};

/** The four measures of DESIGN.md §13, in the order of the sort row. */
export const MEASURES: readonly Measure[] = [
  {
    sort: "credits",
    name: "Tokens",
    caption: "Tokens held right now.",
    from: "min_credits_cr",
    to: "max_credits_cr",
    kind: "amount",
  },
  {
    sort: "win_rate",
    name: "Win rate",
    caption: "Share of resolved markets that returned more than was put in.",
    from: "min_win_rate",
    to: "max_win_rate",
    kind: "percent",
  },
  {
    sort: "trades",
    name: "Trades",
    caption: "Number of trades made.",
    from: "min_trades",
    to: "max_trades",
    kind: "count",
  },
  {
    sort: "avg_payout_ratio",
    name: "Payout",
    caption: "Tokens returned per token put in, over resolved markets.",
    from: "min_avg_payout_ratio",
    to: "max_avg_payout_ratio",
    kind: "ratio",
  },
];

export function measureOf(sort: LeaderboardSort): Measure {
  return MEASURES.find((measure) => measure.sort === sort) ?? MEASURES[0]!;
}

/** The two directions, by the name the order button and the table headings give them. */
export const ORDER_NAMES: Record<SortOrder, string> = {
  desc: "Highest first",
  asc: "Lowest first",
};

/** The list opens on "Tokens", "Highest first", without filters. */
export const OPENING_VIEW: View = { sort: "credits", order: "desc", filters: {} };

/* ---- The address ------------------------------------------------------------------- */

/**
 * Reads a view from the query string of the address. A sort or an order the screen has
 * no control for falls back to the one the list opens on; a filter is taken as it stands,
 * whatever it says, because the API is the one that judges it.
 */
export function readView(search: Pick<URLSearchParams, "get">): View {
  const sort = search.get("sort");
  const filters: Filters = {};
  for (const name of FILTER_NAMES) {
    const value = search.get(name);
    if (value !== null && value !== "") filters[name] = value;
  }
  return {
    sort: MEASURES.find((measure) => measure.sort === sort)?.sort ?? OPENING_VIEW.sort,
    order: search.get("order") === "asc" ? "asc" : OPENING_VIEW.order,
    filters,
  };
}

/**
 * The query string of a view, without its `?`. What the list opens on is left out, so
 * the plain address is the plain list.
 */
export function queryOf(view: View): string {
  const query = new URLSearchParams();
  if (view.sort !== OPENING_VIEW.sort) query.set("sort", view.sort);
  if (view.order !== OPENING_VIEW.order) query.set("order", view.order);
  for (const name of FILTER_NAMES) {
    const value = view.filters[name];
    if (value !== undefined) query.set(name, value);
  }
  return query.toString();
}

/**
 * The query of `GET /leaderboard` for a view: the sort and the order, always both, and
 * every filter that is set. No `limit`: the page size is the API's. Each filter is the
 * text of the address, which goes out unchanged: the API judges exactly what was typed.
 */
export function requestOf(view: View): LeaderboardParams {
  return { sort: view.sort, order: view.order, ...view.filters };
}

/* ---- The filter sheet -------------------------------------------------------------- */

/**
 * The fields of the filter sheet as they are typed: every filter by its name, empty while
 * it is not set. The two bounds of the win rate are held in percent, as the user reads
 * and types them.
 */
export type Draft = Record<FilterName, string>;

function kindOf(name: FilterName): BoundKind | null {
  const measure = MEASURES.find((candidate) => candidate.from === name || candidate.to === name);
  return measure?.kind ?? null;
}

/** The fields for the filters that are set. */
export function draftOf(filters: Filters): Draft {
  const draft = Object.fromEntries(FILTER_NAMES.map((name) => [name, ""])) as Draft;
  for (const name of FILTER_NAMES) {
    const value = filters[name];
    if (value === undefined) continue;
    draft[name] = kindOf(name) === "percent" ? (fractionToPercent(value) ?? value) : value;
  }
  return draft;
}

/**
 * What a field keeps of what is typed into it: digits for a whole number; digits, points
 * and commas for the others. Nothing else can be part of a figure, so nothing else shows.
 */
export function cleanBound(kind: BoundKind, typed: string): string {
  return typed.replace(kind === "count" ? /\D/g : /[^\d.,]/g, "");
}

/** A figure with its thousands grouped, the way the app itself writes one: `1,204.5`. */
const GROUPED = /^[1-9]\d{0,2}(,\d{3})+(\.\d+)?$/;
/** A figure with a decimal comma, which is what the decimal key of many phones here types. */
const DECIMAL_COMMA = /^\d*,\d+$/;

/**
 * A figure as it was typed, as the plain decimal the API reads. This is spelling, not
 * arithmetic, and it only settles what a comma means:
 *
 * - `1,204.5` is read the way the app writes it: the commas group thousands and go.
 * - `62,5` has a decimal comma: it becomes a point.
 * - A separator nothing follows is dropped (`5.` is `5`), and a figure that starts with
 *   one gets its zero (`.5` is `0.5`).
 *
 * Anything else stays as it is, and the API says on the field what it makes of it.
 */
function finish(typed: string): string {
  let text = typed.trim().replace(/[.,]$/, "");
  if (GROUPED.test(text)) text = text.replace(/,/g, "");
  else if (DECIMAL_COMMA.test(text)) text = text.replace(",", ".");
  return text.startsWith(".") ? `0${text}` : text;
}

/** The fields as they are sent: each bound finished, the rest as it stands. */
export function finishDraft(draft: Draft): Draft {
  const finished = { ...draft };
  for (const name of BOUND_NAMES) finished[name] = finish(draft[name]);
  return finished;
}

/**
 * The filters a finished draft stands for. A percentage becomes the fraction the API
 * takes by moving its decimal point in the string; every other field is sent as typed.
 * What is no percentage goes out as it is, and the API says so on its field.
 */
export function filtersOf(draft: Draft): Filters {
  const filters: Filters = {};
  for (const name of FILTER_NAMES) {
    const text = draft[name];
    if (text === "") continue;
    filters[name] = kindOf(name) === "percent" ? (percentToFraction(text) ?? text) : text;
  }
  return filters;
}

/* ---- Filters that are set ---------------------------------------------------------- */

/** A filter that is set, as its chip under the sort row states it. */
export type SetFilter = {
  id: string;
  /** "BSc Computer Science", "Win rate from 50%". */
  label: string;
  /** What removing the chip removes. */
  names: readonly FilterName[];
};

/** A bound as a chip shows it: the text of the address in the unit of its measure. */
function boundText(kind: BoundKind, value: string): string {
  switch (kind) {
    case "amount":
      return formatAmount(value, { full: true });
    case "percent": {
      const percent = fractionToPercent(value);
      return percent === null ? value : `${percent}%`;
    }
    case "ratio":
      return `${value}${TIMES}`;
    case "count":
      return value;
  }
}

/**
 * The chips of the filters that are set. The degree is one chip: the programme as sent,
 * or the level by its `name` from `/config` while no programme is chosen. Removing it
 * removes both, because a programme belongs to its level.
 */
export function setFilters(
  filters: Filters,
  degrees: readonly DegreeLevelOption[],
): readonly SetFilter[] {
  const set: SetFilter[] = [];
  const { degree_level, degree_programme } = filters;
  if (degree_level !== undefined || degree_programme !== undefined) {
    const level = degrees.find((degree) => degree.level === degree_level);
    set.push({
      id: "degree",
      label: degree_programme ?? level?.name ?? degree_level ?? "",
      names: ["degree_level", "degree_programme"],
    });
  }
  for (const measure of MEASURES) {
    const from = filters[measure.from];
    const to = filters[measure.to];
    if (from !== undefined) {
      set.push({
        id: measure.from,
        label: `${measure.name} from ${boundText(measure.kind, from)}`,
        names: [measure.from],
      });
    }
    if (to !== undefined) {
      set.push({
        id: measure.to,
        label: `${measure.name} to ${boundText(measure.kind, to)}`,
        names: [measure.to],
      });
    }
  }
  return set;
}

/** The filters without the ones named. */
export function withoutFilters(filters: Filters, names: readonly FilterName[]): Filters {
  const kept: Filters = {};
  for (const name of FILTER_NAMES) {
    const value = filters[name];
    if (value !== undefined && !names.includes(name)) kept[name] = value;
  }
  return kept;
}

/* ---- The podium -------------------------------------------------------------------- */

/**
 * Whether a row has a figure for a measure. A player without a resolved market has no
 * win rate and no payout ratio; tokens and trades are always there.
 */
export function hasFigure(entry: LeaderboardEntry, sort: LeaderboardSort): boolean {
  if (sort === "win_rate") return entry.win_rate !== null;
  if (sort === "avg_payout_ratio") return entry.avg_payout_ratio !== null;
  return true;
}

export const PODIUM_PLACES = [1, 2, 3] as const;
export type PodiumPlace = (typeof PODIUM_PLACES)[number];

/** Who stands on each place of the podium, first to third. Null is an empty place. */
export type PodiumRows = readonly [
  LeaderboardEntry | null,
  LeaderboardEntry | null,
  LeaderboardEntry | null,
];

export type Arrangement = {
  /** The podium of a "Highest first" list. A "Lowest first" list has none. */
  podium: PodiumRows | null;
  /** Every row that is not on the podium, in the order the API sent them. */
  rows: readonly LeaderboardEntry[];
};

/**
 * Where each row of the answer is shown (DESIGN.md §13). The rows of `rank` 1, 2 and 3
 * of a "Highest first" list stand on the podium, each on the place its rank names. A
 * place needs a figure: it stays empty when the list has no row of that rank, or when
 * that row has no figure for the sorted measure, and such a row is listed with the
 * others. The rows keep the order they arrived in; this decides where a row is drawn,
 * never which rows there are or how they rank.
 */
export function arrange(
  entries: readonly LeaderboardEntry[],
  sort: LeaderboardSort,
  order: SortOrder,
): Arrangement {
  if (order !== "desc") return { podium: null, rows: entries };

  const place = (rank: PodiumPlace): LeaderboardEntry | null => {
    const entry = entries.find((candidate) => candidate.rank === rank);
    return entry !== undefined && hasFigure(entry, sort) ? entry : null;
  };
  const podium: PodiumRows = [place(1), place(2), place(3)];

  const rows: LeaderboardEntry[] = [];
  for (const entry of entries) if (!podium.includes(entry)) rows.push(entry);
  return { podium, rows };
}

/** Whether a row is one of the three podium rows of this view: it then carries a trophy. */
export function isPodiumRow(entry: LeaderboardEntry, sort: LeaderboardSort, order: SortOrder) {
  return (
    order === "desc" && PODIUM_PLACES.some((rank) => rank === entry.rank) && hasFigure(entry, sort)
  );
}

/**
 * Where the viewer's own row is drawn: on the podium, in the rows at an index, or
 * nowhere (null) when it is not among the rows that are loaded. The row is the one the
 * API marks `is_viewer`.
 */
export function viewerPlacement({ podium, rows }: Arrangement): string | null {
  const place = podium?.findIndex((entry) => entry?.is_viewer === true) ?? -1;
  if (place !== -1) return `podium:${place}`;
  const index = rows.findIndex((entry) => entry.is_viewer);
  return index === -1 ? null : `row:${index}`;
}
