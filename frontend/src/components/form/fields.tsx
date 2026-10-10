"use client";

import type { ComponentProps } from "react";

import { Input } from "@/components/ui/input";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import { useConfig } from "@/lib/api/session";
import { formatCode, fromDateTimeInput, toDateTimeInput } from "@/lib/format";
import { cn } from "@/lib/utils";

import { Field, type FieldProps } from "./field";

type Wired = "id" | "aria-describedby" | "aria-invalid";

/** `{typed} / {limit}` under a text field whose limit comes from `/config`. */
function count(value: unknown, limit: number | undefined, show: boolean) {
  if (!show || limit === undefined || typeof value !== "string") return undefined;
  return `${value.length} / ${limit}`;
}

export type TextFieldProps = FieldProps &
  Omit<ComponentProps<"input">, Wired | "className"> & {
    /** Show how much of `maxLength` is used. */
    showCount?: boolean;
    inputClassName?: string;
  };

/** A one-line text field: 48 px, 16 px text. */
export function TextField({
  label,
  help,
  error,
  counter,
  className,
  showCount = false,
  inputClassName,
  ...input
}: TextFieldProps) {
  return (
    <Field
      label={label}
      help={help}
      error={error}
      counter={counter ?? count(input.value, input.maxLength, showCount)}
      className={className}
    >
      {(control) => <Input {...input} {...control} className={inputClassName} />}
    </Field>
  );
}

export type TextAreaFieldProps = FieldProps &
  Omit<ComponentProps<"textarea">, Wired | "className"> & {
    showCount?: boolean;
  };

/** A text field of several lines, which grows with what is typed. */
export function TextAreaField({
  label,
  help,
  error,
  counter,
  className,
  showCount = false,
  ...textarea
}: TextAreaFieldProps) {
  return (
    <Field
      label={label}
      help={help}
      error={error}
      counter={counter ?? count(textarea.value, textarea.maxLength, showCount)}
      className={className}
    >
      {(control) => <Textarea {...textarea} {...control} />}
    </Field>
  );
}

export type SelectFieldProps = FieldProps &
  Omit<ComponentProps<"select">, Wired | "className" | "children" | "value" | "onChange"> & {
    /** The chosen value, or the empty string while nothing is chosen. */
    value: string;
    onChange: (value: string) => void;
    options: readonly { value: string; label: string }[];
    /** What the field says while nothing is chosen. It cannot be chosen itself. */
    placeholder: string;
  };

/**
 * A choice from a fixed list: the phone's own picker, styled like a field (DESIGN.md
 * §12). A select that depends on another is `disabled` until the other is set.
 */
export function SelectField({
  label,
  help,
  error,
  counter,
  className,
  value,
  onChange,
  options,
  placeholder,
  ...select
}: SelectFieldProps) {
  return (
    <Field label={label} help={help} error={error} counter={counter} className={className}>
      {(control) => (
        <NativeSelect
          {...select}
          {...control}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          className={cn(value === "" && "text-muted-foreground")}
        >
          <NativeSelectOption value="" disabled>
            {placeholder}
          </NativeSelectOption>
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

export type DateTimeFieldProps = FieldProps &
  Omit<
    ComponentProps<"input">,
    Wired | "className" | "type" | "value" | "onChange" | "min" | "max"
  > & {
    /** The chosen instant as ISO 8601 with its offset, or null while the field is empty. */
    value: string | null;
    onChange: (value: string | null) => void;
    /** The earliest and latest instant the picker offers, as ISO 8601. */
    min?: string;
    max?: string;
  };

/**
 * A date and a time (DESIGN.md §10): entered and shown in the `timezone` of `/config`,
 * whatever the device is set to, and handed on as ISO 8601 with that zone's offset,
 * ready to be sent.
 */
export function DateTimeField({
  label,
  help,
  error,
  counter,
  className,
  value,
  onChange,
  min,
  max,
  ...input
}: DateTimeFieldProps) {
  const { timezone } = useConfig();

  return (
    <Field label={label} help={help} error={error} counter={counter} className={className}>
      {(control) => (
        <Input
          {...input}
          {...control}
          type="datetime-local"
          value={value === null ? "" : toDateTimeInput(value, timezone)}
          min={min === undefined ? undefined : toDateTimeInput(min, timezone)}
          max={max === undefined ? undefined : toDateTimeInput(max, timezone)}
          onChange={(event) => onChange(fromDateTimeInput(event.target.value, timezone))}
        />
      )}
    </Field>
  );
}

export type CodeInputProps = FieldProps &
  Omit<ComponentProps<"input">, Wired | "className" | "value" | "onChange" | "maxLength"> & {
    /** What the field shows: the code in groups of four. */
    value: string;
    onChange: (value: string) => void;
    /** `code_length` or `voucher_code_length` of `/config`: the characters of a code. */
    length: number;
  };

/**
 * The field of a code or a voucher code (DESIGN.md §12): the code style, capitals, and
 * the dashes inserted as the user types. What it holds is what is sent; the server reads
 * a code with or without dashes.
 *
 * It keeps no more than `length` characters by cutting what was typed, not with a
 * `maxLength`: that would cut a pasted code with its spaces or dashes short.
 */
export function CodeInput({
  label,
  help,
  error,
  counter,
  className,
  value,
  onChange,
  length,
  ...input
}: CodeInputProps) {
  return (
    <Field label={label} help={help} error={error} counter={counter} className={className}>
      {(control) => (
        <Input
          {...input}
          {...control}
          value={value}
          onChange={(event) => onChange(formatCode(event.target.value, length))}
          placeholder={input.placeholder ?? formatCode("X".repeat(length))}
          autoCapitalize="characters"
          autoCorrect="off"
          autoComplete="off"
          spellCheck={false}
          className="type-code"
        />
      )}
    </Field>
  );
}
