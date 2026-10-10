"use client";

import { useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useId, useRef, useState } from "react";

import { ErrorBanner, InlineError, RetryCountdown } from "@/components/feedback";
import { AmountField, Field, PickerField, revealFirstInvalid } from "@/components/form";
import { ResponsiveSheet, SheetBody, SheetFooter } from "@/components/sheet";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { type ApiError, isApiError, placeError } from "@/lib/api/errors";
import { leaderboardQuery } from "@/lib/api/queries";
import { useConfig } from "@/lib/api/session";
import { TIMES, typedDecimal } from "@/lib/format";
import { useRetryAfter } from "@/lib/hooks";

import {
  type BoundKind,
  type Draft,
  draftOf,
  FILTER_NAMES,
  type FilterName,
  type Filters,
  filtersOf,
  type Measure,
  MEASURES,
  requestOf,
  type View,
} from "./view";

/* The unit a bound is typed in, shown inside the end of its field and said with its group. */
const UNITS: Record<BoundKind, { shown: string; said: string } | null> = {
  amount: null,
  percent: { shown: "%", said: "in percent" },
  count: null,
  ratio: { shown: TIMES, said: "tokens returned per token put in" },
};

export type FilterSheetProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** What the list shows: the fields start from its filters, and its sort and order are kept. */
  view: View;
  /**
   * The list's own `VALIDATION_ERROR`, when the address already holds a filter the API
   * rejects: the sheet opens with it on the field it names.
   */
  rejected: ApiError | null;
  /** Called with filters the API has accepted, or with none after "Clear all". */
  onApply: (filters: Filters) => void;
};

/**
 * The filter sheet of the leaderboard (DESIGN.md §13): the bottom sheet below 768 px and
 * the dialog from 768 px, with the title "Filter" and the close cross. The part that
 * scrolls holds a Level and a Programme picker from `/config` `degrees` (DESIGN.md §12),
 * each with a first option that stands for no filter, then one group per measure with a
 * "From" and a "To" field in two equal columns. The foot is pinned
 * under its line: a primary "Show results" and a ghost "Clear all".
 *
 * The API is the judge of every value. "Show results" asks it for the list with these
 * filters; only when it answers does the address change, so a value it rejects
 * (`VALIDATION_ERROR`) is marked on the field the answer names, with the API's sentence,
 * while the list behind the sheet keeps its last result. Nothing is checked here: a
 * "from" is not compared with a "to", and a figure goes out with the characters that were
 * typed (a percentage as the fraction the API takes, by moving its point in the string).
 *
 * The fields are kept outside the sheet's content, which is mounted afresh when a phone
 * is turned across 768 px.
 */
export function FilterSheet({ open, onOpenChange, view, rejected, onApply }: FilterSheetProps) {
  const { degrees } = useConfig();
  const client = useQueryClient();
  const [draft, setDraft] = useState<Draft>(() => draftOf(view.filters));
  const [problem, setProblem] = useState<ApiError | null>(rejected);
  const [checking, setChecking] = useState(false);
  const [wasOpen, setWasOpen] = useState(open);
  /** Counts what the sheet has asked and given up on: an answer nobody waits for changes nothing. */
  const asked = useRef(0);
  const wait = useRetryAfter(problem);

  // The fields scroll under the buttons: a field the API refused is brought into view,
  // with its message. Only an answer to "Show results" does this; a sheet that opens on
  // a filter the address already held leaves the focus where the sheet put it.
  const form = useRef<HTMLFormElement>(null);
  const [refusals, setRefusals] = useState(0);
  useEffect(() => {
    if (refusals > 0) revealFirstInvalid(form.current);
  }, [refusals]);

  // Every opening starts from what the list shows.
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) {
      setDraft(draftOf(view.filters));
      setProblem(rejected);
      setChecking(false);
    }
  }

  const placed = problem === null ? null : placeError(problem, FILTER_NAMES);
  const programmes = degrees.find((degree) => degree.level === draft.degree_level)?.programmes;
  const nothingSet =
    FILTER_NAMES.every((name) => draft[name] === "") && Object.keys(view.filters).length === 0;

  const giveUp = () => {
    asked.current += 1;
  };

  /** An answer is about what was sent: it holds until a field changes. */
  const change = (name: FilterName, text: string) => {
    giveUp();
    setChecking(false);
    setProblem(null);
    setDraft((was) => ({
      ...was,
      [name]: text,
      // A programme belongs to its level.
      ...(name === "degree_level" ? { degree_programme: "" } : null),
    }));
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (wait > 0) return;
    const filters = filtersOf(draft);
    asked.current += 1;
    const mine = asked.current;
    setChecking(true);
    setProblem(null);
    try {
      // The first page under these filters: the answer the list will show.
      await client.fetchInfiniteQuery(leaderboardQuery(requestOf({ ...view, filters })));
    } catch (error) {
      if (asked.current !== mine) return;
      setChecking(false);
      setProblem(isApiError(error) ? error : null);
      setRefusals((count) => count + 1);
      return;
    }
    if (asked.current !== mine) return;
    setChecking(false);
    onApply(filters);
  };

  return (
    <ResponsiveSheet
      open={open}
      onOpenChange={(next) => {
        // Closing the sheet takes back a question that is still open.
        if (!next) giveUp();
        onOpenChange(next);
      }}
      title="Filter"
    >
      <form ref={form} noValidate onSubmit={submit} className="flex min-h-0 flex-1 flex-col">
        <SheetBody>
          <PickerField
            label="Level"
            name="degree_level"
            any="Any level"
            value={draft.degree_level}
            onChange={(value) => change("degree_level", value)}
            options={degrees.map((degree) => ({ value: degree.level, label: degree.name }))}
            placeholder="Choose a level"
            searchLabel="Search levels"
            noMatch="No level matches."
            error={placed?.fields.degree_level}
          />
          <PickerField
            label="Programme"
            name="degree_programme"
            any="Any programme"
            value={draft.degree_programme}
            onChange={(value) => change("degree_programme", value)}
            options={(programmes ?? []).map((programme) => ({
              value: programme,
              label: programme,
            }))}
            placeholder="Choose a programme"
            searchLabel="Search programmes"
            noMatch="No programme matches."
            disabled={draft.degree_level === ""}
            error={placed?.fields.degree_programme}
          />

          {MEASURES.map((measure) => (
            <BoundRow
              key={measure.sort}
              measure={measure}
              draft={draft}
              errors={placed?.fields}
              onChange={change}
            />
          ))}
        </SheetBody>

        <SheetFooter>
          {problem !== null && placed?.form != null && (
            <div className="flex flex-col gap-1">
              {problem.outcomeUnknown ? (
                <ErrorBanner error={problem} />
              ) : (
                <InlineError>{placed.form}</InlineError>
              )}
              <RetryCountdown error={problem} />
            </div>
          )}
          <Button type="submit" width="full" loading={checking} disabled={wait > 0}>
            Show results
          </Button>
          <Button
            variant="ghost"
            width="full"
            disabled={nothingSet}
            onClick={() => {
              giveUp();
              onApply({});
            }}
          >
            Clear all
          </Button>
        </SheetFooter>
      </form>
    </ResponsiveSheet>
  );
}

/**
 * The group of one measure: its name, and a "From" and a "To" field side by side in two
 * equal columns, 12 px apart. The two are aligned at their tops, so an error under one
 * does not move the other. The unit the two are typed in stands inside each field and is
 * said with the group's name.
 */
function BoundRow({
  measure,
  draft,
  errors,
  onChange,
}: {
  measure: Measure;
  draft: Draft;
  /** The API's sentence for each field it refused. */
  errors: Partial<Record<FilterName, string>> | undefined;
  onChange: (name: FilterName, text: string) => void;
}) {
  const nameId = useId();
  const partsId = useId();
  const unit = UNITS[measure.kind];

  return (
    <div
      role="group"
      aria-labelledby={nameId}
      aria-describedby={measure.parts === undefined ? undefined : partsId}
      className="flex flex-col gap-2"
    >
      <div className="flex flex-wrap items-baseline gap-x-2">
        <p id={nameId} className="type-label">
          {measure.name}
          {unit !== null && <span className="sr-only">, {unit.said}</span>}
        </p>
        {measure.parts !== undefined && (
          <p id={partsId} className="type-caption text-muted-foreground">
            {measure.parts}
          </p>
        )}
      </div>
      <div className="grid grid-cols-2 items-start gap-3">
        <BoundField
          label="From"
          name={measure.from}
          kind={measure.kind}
          value={draft[measure.from]}
          onChange={(text) => onChange(measure.from, text)}
          error={errors?.[measure.from]}
        />
        <BoundField
          label="To"
          name={measure.to}
          kind={measure.kind}
          value={draft[measure.to]}
          onChange={(text) => onChange(measure.to, text)}
          error={errors?.[measure.to]}
        />
      </div>
    </div>
  );
}

/**
 * One end of a measure's range: a field for a figure (DESIGN.md §12), which keeps digits
 * and one decimal point, or digits alone for a whole number, and holds what is sent. A
 * field typed in percent or as a ratio shows its unit inside its right end, and its text
 * ends 36 px before that end: what is typed never runs under the unit.
 */
function BoundField({
  label,
  name,
  kind,
  value,
  onChange,
  error,
}: {
  label: string;
  name: string;
  kind: BoundKind;
  value: string;
  onChange: (text: string) => void;
  error?: string;
}) {
  const unit = UNITS[kind];

  if (unit === null) {
    return (
      <AmountField
        label={label}
        name={name}
        value={value}
        onChange={onChange}
        whole={kind === "count"}
        error={error}
      />
    );
  }

  return (
    <Field label={label} error={error}>
      {(control) => (
        <div className="relative">
          <Input
            {...control}
            name={name}
            value={value}
            onChange={(event) => onChange(typedDecimal(event.target.value))}
            inputMode="decimal"
            autoComplete="off"
            className="pr-9"
          />
          <span
            aria-hidden
            className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 type-body text-muted-foreground"
          >
            {unit.shown}
          </span>
        </div>
      )}
    </Field>
  );
}
