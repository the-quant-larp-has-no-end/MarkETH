"use client";

import { DateTimeField, TextAreaField, TextField } from "@/components/form";
import { useConfig } from "@/lib/api/session";
import { formatCount } from "@/lib/format";

/**
 * What a market states, under the names the API gives it: the wording and the two times.
 * A proposal sets all seven (`POST /markets`), and an approval may change any of them.
 */
export const TERM_FIELDS = [
  "question",
  "yes_definition",
  "no_definition",
  "resolution_criteria",
  "resolution_source",
  "close_at",
  "resolve_by",
] as const;

export type TermField = (typeof TERM_FIELDS)[number];

export type MarketTerms = {
  question: string;
  yes_definition: string;
  no_definition: string;
  resolution_criteria: string;
  resolution_source: string;
  /** ISO 8601 with its offset, or null while the field is empty. */
  close_at: string | null;
  resolve_by: string | null;
};

/** `MarketTerms` with every field stated: what can be sent. */
export type StatedTerms = { [Field in TermField]: string };

export const EMPTY_TERMS: MarketTerms = {
  question: "",
  yes_definition: "",
  no_definition: "",
  resolution_criteria: "",
  resolution_source: "",
  close_at: null,
  resolve_by: null,
};

/**
 * The terms as they are sent: each text without the blanks around it, each time as the
 * field produced it. Null while one of the seven is still empty. Whether what is stated
 * is acceptable (a length, a time in the future) is for the API to say.
 */
export function statedTerms(terms: MarketTerms): StatedTerms | null {
  const stated = {
    question: terms.question.trim(),
    yes_definition: terms.yes_definition.trim(),
    no_definition: terms.no_definition.trim(),
    resolution_criteria: terms.resolution_criteria.trim(),
    resolution_source: terms.resolution_source.trim(),
    close_at: terms.close_at ?? "",
    resolve_by: terms.resolve_by ?? "",
  };
  return TERM_FIELDS.every((field) => stated[field] !== "") ? stated : null;
}

function days(count: number): string {
  return count === 1 ? "1 day" : `${formatCount(count)} days`;
}

/**
 * The seven fields of a market's terms, one per row, in the order of DESIGN.md §13:
 * "Question", the two options, "How it is decided", "Source", "Trading closes"
 * and "Decided by". Creation labels the options as optional; approval states what
 * YES and NO mean.
 *
 * Every limit comes from `/config`: the lengths of the texts, which each field counts
 * down, and the days the two times may lie ahead, which their help text states. The times
 * are entered and shown in the `timezone` of `/config` and handed on as ISO 8601 with
 * their offset. Nothing is checked here: the API's message for a field arrives in `errors`.
 */
export function MarketTermsFields({
  value,
  onChange,
  errors,
  disabled = false,
  optionalOptions = false,
}: {
  value: MarketTerms;
  onChange: (terms: MarketTerms) => void;
  /** The API's message for each field it refused (`placeError`). */
  errors?: Partial<Record<TermField, string>>;
  disabled?: boolean;
  /** Creation offers YES / NO defaults that typing replaces. */
  optionalOptions?: boolean;
}) {
  const config = useConfig();
  const set = <Field extends TermField>(field: Field, next: MarketTerms[Field]) =>
    onChange({ ...value, [field]: next });
  const selectDefault = (field: HTMLTextAreaElement, defaultValue: string) => {
    if (optionalOptions && field.value === defaultValue) field.select();
  };

  return (
    <>
      <TextAreaField
        label="Question"
        name="question"
        value={value.question}
        onChange={(event) => set("question", event.target.value)}
        maxLength={config.question_max_length}
        showCount
        help={`${formatCount(config.question_min_length)} to ${formatCount(config.question_max_length)} characters.`}
        error={errors?.question}
        disabled={disabled}
      />
      <TextAreaField
        label={optionalOptions ? "Option 1" : "YES means"}
        name="yes_definition"
        value={value.yes_definition}
        onChange={(event) => set("yes_definition", event.target.value)}
        onFocus={(event) => selectDefault(event.currentTarget, "YES")}
        onClick={(event) => selectDefault(event.currentTarget, "YES")}
        maxLength={config.short_text_max_length}
        showCount
        help={optionalOptions ? "Defaults to YES if left empty." : undefined}
        error={errors?.yes_definition}
        disabled={disabled}
      />
      <TextAreaField
        label={optionalOptions ? "Option 2" : "NO means"}
        name="no_definition"
        value={value.no_definition}
        onChange={(event) => set("no_definition", event.target.value)}
        onFocus={(event) => selectDefault(event.currentTarget, "NO")}
        onClick={(event) => selectDefault(event.currentTarget, "NO")}
        maxLength={config.short_text_max_length}
        showCount
        help={optionalOptions ? "Defaults to NO if left empty." : undefined}
        error={errors?.no_definition}
        disabled={disabled}
      />
      <TextAreaField
        label="How it is decided"
        name="resolution_criteria"
        value={value.resolution_criteria}
        onChange={(event) => set("resolution_criteria", event.target.value)}
        maxLength={config.long_text_max_length}
        showCount
        error={errors?.resolution_criteria}
        disabled={disabled}
      />
      <TextField
        label="Source"
        name="resolution_source"
        value={value.resolution_source}
        onChange={(event) => set("resolution_source", event.target.value)}
        maxLength={config.short_text_max_length}
        showCount
        autoComplete="off"
        error={errors?.resolution_source}
        disabled={disabled}
      />
      <DateTimeField
        label="Trading closes"
        name="close_at"
        value={value.close_at}
        onChange={(next) => set("close_at", next)}
        help={`At most ${days(config.max_market_lifetime_days)} from now. ${config.timezone} time.`}
        error={errors?.close_at}
        disabled={disabled}
      />
      <DateTimeField
        label="Decided by"
        name="resolve_by"
        value={value.resolve_by}
        onChange={(next) => set("resolve_by", next)}
        help={`At most ${days(config.max_resolution_delay_days)} after trading closes. ${config.timezone} time.`}
        error={errors?.resolve_by}
        disabled={disabled}
      />
    </>
  );
}
