"use client";

import { useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useId, useRef, useState } from "react";

import { ErrorBanner, InlineError, RetryCountdown } from "@/components/feedback";
import { Field } from "@/components/form";
import { ResponsiveSheet, SheetBody, SheetFooter } from "@/components/sheet";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { type ApiError, isApiError, placeError } from "@/lib/api/errors";
import { leaderboardQuery } from "@/lib/api/queries";
import { useConfig } from "@/lib/api/session";
import { TIMES } from "@/lib/format";
import { useRetryAfter } from "@/lib/hooks";

import {
  type BoundKind,
  cleanBound,
  type Draft,
  draftOf,
  FILTER_NAMES,
  type FilterName,
  type Filters,
  filtersOf,
  finishDraft,
  type Measure,
  MEASURES,
  requestOf,
  type View,
} from "./view";

/* The unit a bound is typed in, shown at the end of its field and said with its row. */
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
 * The filter sheet of the leaderboard (DESIGN.md §13): a Level and a Programme select
 * from `/config` `degrees`, then one row per measure with a "from" and a "to" field, a
 * primary "Show results" and a ghost "Clear all".
 *
 * The API is the judge of every value. "Show results" asks it for the list with these
 * filters; only when it answers does the address change, so a value it rejects
 * (`VALIDATION_ERROR`) is marked on its field with its message while the list behind the
 * sheet keeps its last result. Nothing is checked here: a "from" is not compared with a
 * "to", and a figure goes out as the digits that were typed (a percentage as the fraction
 * the API takes, by moving its point in the string).
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

  /** An answer is about what was sent: once a field changes, it no longer applies. */
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
    // The fields then show what is sent: `1,204.5` as `1204.5`, `62,5` as `62.5`.
    const finished = finishDraft(draft);
    const filters = filtersOf(finished);
    asked.current += 1;
    const mine = asked.current;
    setDraft(finished);
    setChecking(true);
    setProblem(null);
    try {
      // The first page under these filters: the answer the list will show.
      await client.fetchInfiniteQuery(leaderboardQuery(requestOf({ ...view, filters })));
    } catch (error) {
      if (asked.current !== mine) return;
      setChecking(false);
      setProblem(isApiError(error) ? error : null);
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
      <form noValidate onSubmit={submit} className="flex min-h-0 flex-1 flex-col">
        <SheetBody>
          <ChoiceField
            label="Level"
            name="degree_level"
            any="Any level"
            value={draft.degree_level}
            onChange={(value) => change("degree_level", value)}
            options={degrees.map((degree) => ({ value: degree.level, label: degree.name }))}
            error={placed?.fields.degree_level}
          />
          <ChoiceField
            label="Programme"
            name="degree_programme"
            any="Any programme"
            value={draft.degree_programme}
            onChange={(value) => change("degree_programme", value)}
            options={(programmes ?? []).map((programme) => ({
              value: programme,
              label: programme,
            }))}
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
 * A select of the filter sheet: the phone's own picker, styled like a field. Unlike a
 * form's select it can be set back, to the option that stands for no filter.
 */
function ChoiceField({
  label,
  name,
  any,
  value,
  onChange,
  options,
  disabled = false,
  error,
}: {
  label: string;
  name: string;
  /** What the select says while it filters nothing: "Any level". */
  any: string;
  value: string;
  onChange: (value: string) => void;
  options: readonly { value: string; label: string }[];
  disabled?: boolean;
  error?: string;
}) {
  // A value the address brought that is not on the list is still what is set.
  const listed = value === "" || options.some((option) => option.value === value);

  return (
    <Field label={label} error={error}>
      {(control) => (
        <NativeSelect
          {...control}
          name={name}
          value={value}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
        >
          <NativeSelectOption value="">{any}</NativeSelectOption>
          {!listed && <NativeSelectOption value={value}>{value}</NativeSelectOption>}
          {options.map((option) => (
            <NativeSelectOption key={option.value} value={option.value}>
              {option.label}
            </NativeSelectOption>
          ))}
        </NativeSelect>
      )}
    </Field>
  );
}

/**
 * The row of one measure: its name, and a "from" and a "to" field side by side. The unit
 * the two are typed in stands at the end of each field and is said with the row's name.
 */
function BoundRow({
  measure,
  draft,
  errors,
  onChange,
}: {
  measure: Measure;
  draft: Draft;
  /** The API's message for each field it rejected. */
  errors: Partial<Record<FilterName, string>> | undefined;
  onChange: (name: FilterName, text: string) => void;
}) {
  const nameId = useId();
  const unit = UNITS[measure.kind];

  return (
    <div role="group" aria-labelledby={nameId} className="flex flex-col gap-2">
      <p id={nameId} className="type-label">
        {measure.name}
        {unit !== null && <span className="sr-only">, {unit.said}</span>}
      </p>
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

/** One end of a measure's range: a field that keeps the characters a figure can have. */
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

  return (
    <Field label={label} error={error}>
      {(control) => (
        <div className="relative">
          <Input
            {...control}
            name={name}
            value={value}
            onChange={(event) => onChange(cleanBound(kind, event.target.value))}
            inputMode={kind === "count" ? "numeric" : "decimal"}
            autoComplete="off"
            className={unit === null ? undefined : "pr-9"}
          />
          {unit !== null && (
            <span
              aria-hidden
              className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 type-body text-muted-foreground"
            >
              {unit.shown}
            </span>
          )}
        </div>
      )}
    </Field>
  );
}
