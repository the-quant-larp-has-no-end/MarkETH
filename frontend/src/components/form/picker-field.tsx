"use client";

import { CheckIcon, ChevronDownIcon } from "lucide-react";
import { type KeyboardEvent, useEffect, useId, useRef, useState } from "react";

import { ResponsiveSheet } from "@/components/sheet";
import { fieldClasses, Input } from "@/components/ui/input";
import { WIDE_QUERY } from "@/lib/design";
import { useMediaQuery } from "@/lib/hooks";
import { cn } from "@/lib/utils";

import { type ControlProps, Field, type FieldProps } from "./field";

type Option = { value: string; label: string };

export type PickerFieldProps = FieldProps & {
  name: string;
  /** The chosen option's value, or "" while nothing is chosen. */
  value: string;
  onChange: (value: string) => void;
  /** In the order to offer them. The picker never reorders. */
  options: readonly Option[];
  /** What the field says while nothing is chosen: "Choose a programme". */
  placeholder: string;
  /** The name of the sheet's search field: "Search programmes". */
  searchLabel: string;
  /** What the list says when the query finds nothing: "No programme matches." */
  noMatch: string;
  /** The filter sheet: the first option, which stands for no filter and has the value "". */
  any?: string;
  disabled?: boolean;
};

/** Lower case, with diacritics folded: "zurich" finds "Zürich". */
function fold(text: string): string {
  return text
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
}

/**
 * The options a query finds, in the order they were given: the query is split at
 * whitespace, and an option matches when every part occurs in its label ("msc data" finds
 * "MSc Data Science"). Nothing is ranked. An empty query finds every option.
 */
export function matchOptions<T extends { label: string }>(
  options: readonly T[],
  query: string,
): T[] {
  const parts = fold(query)
    .split(/\s+/)
    .filter((part) => part !== "");
  if (parts.length === 0) return [...options];
  return options.filter((option) => {
    const label = fold(option.label);
    return parts.every((part) => label.includes(part));
  });
}

/** What the polite region says after the query changed. */
function resultsText(count: number, noMatch: string): string {
  if (count === 0) return noMatch;
  return count === 1 ? "1 result" : `${count} results`;
}

/**
 * A choice from a fixed list with a search field (DESIGN.md §12, Picker): the degree
 * level and the programme, wherever they are chosen. The field shows the chosen label, or
 * the placeholder in `--muted-foreground`; help text and an error stand under it as under
 * every field.
 *
 *     <PickerField
 *       label="Programme"
 *       name="degree_programme"
 *       value={programme}
 *       onChange={setProgramme}
 *       options={programmes.map((label) => ({ value: label, label }))}
 *       placeholder="Choose a programme"
 *       searchLabel="Search programmes"
 *       noMatch="No programme matches."
 *     />
 *
 * From 768 px it is a combobox in place: typing filters the list under the field. Below
 * it is a button that opens a sheet with a search field over the list; inside another
 * sheet that sheet is nested. Crossing 768 px while the list is open closes it and keeps
 * the choice.
 *
 * The list is the options in the order they were given, after `any` (the option that
 * stands for no filter) and after a value that is set and is not among them: a filter the
 * address brought is shown as its own text, as chosen. Choosing calls `onChange` with a
 * value that differs from the one set, closes the list and returns the focus to the
 * field. The picker posts nothing by itself.
 */
export function PickerField({
  label,
  help,
  error,
  counter,
  className,
  name,
  value,
  onChange,
  options,
  placeholder,
  searchLabel,
  noMatch,
  any,
  disabled = false,
}: PickerFieldProps) {
  const wide = useMediaQuery(WIDE_QUERY);

  const list: Option[] = [];
  if (any !== undefined) list.push({ value: "", label: any });
  if (value !== "" && !options.some((option) => option.value === value)) {
    list.push({ value, label: value });
  }
  list.push(...options);

  const picker: PickerProps = {
    label,
    name,
    value,
    chosen: list.find((option) => option.value === value) ?? null,
    list,
    onChoose: (option) => {
      if (option.value !== value) onChange(option.value);
    },
    placeholder,
    noMatch,
    disabled,
  };

  return (
    <Field label={label} help={help} error={error} counter={counter} className={className}>
      {(control) =>
        wide ? (
          <ComboboxPicker {...picker} control={control} />
        ) : (
          <SheetPicker {...picker} control={control} searchLabel={searchLabel} />
        )
      }
    </Field>
  );
}

type PickerProps = {
  label: string;
  name: string;
  value: string;
  /** The option whose value is set, or null while the placeholder stands. */
  chosen: Option | null;
  /** Every option on offer, in order. */
  list: readonly Option[];
  onChoose: (option: Option) => void;
  placeholder: string;
  noMatch: string;
  disabled: boolean;
};

/**
 * From 768 px: the field is the text input and a combobox, and the list opens under it,
 * over what follows, so the form does not move. Focus selects the text, a click opens the
 * list, and typing filters it: what is typed over the chosen label starts a search of its
 * own. Arrow Down and Arrow Up open the list and move the active option, Home and End
 * take it to the ends, Enter chooses it (or the first match of a query while none is
 * active). Escape, Tab and a click outside close the list and put the chosen label back;
 * the surface around the field stays open.
 */
function ComboboxPicker({
  control,
  label,
  name,
  value,
  chosen,
  list,
  onChoose,
  placeholder,
  noMatch,
  disabled,
}: PickerProps & { control: ControlProps }) {
  const listId = useId();
  const root = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  /** What was typed since the list opened, or null while the field shows the chosen label. */
  const [query, setQuery] = useState<string | null>(null);
  /** The place of the active option among the matches. */
  const [active, setActive] = useState<number | null>(null);

  const matches = matchOptions(list, query ?? "");
  const activeAt = open && active !== null && active < matches.length ? active : null;
  const activeId = activeAt === null ? undefined : `${listId}-${activeAt}`;

  const close = () => {
    setOpen(false);
    setQuery(null);
    setActive(null);
  };
  const choose = (option: Option) => {
    onChoose(option);
    close();
    input.current?.focus();
  };

  // The chosen label is no text to edit. While the field shows it, what is typed starts a
  // search of its own and a deletion empties the field, wherever the caret stands: a
  // click leaves a caret in the label, and typing would add to it.
  const showsLabel = query === null;
  useEffect(() => {
    const field = input.current;
    if (field === null || !showsLabel) return;
    const startSearch = (event: InputEvent) => {
      const deletes = event.inputType.startsWith("delete");
      const inserts =
        event.inputType === "insertText" ||
        event.inputType === "insertReplacementText" ||
        event.inputType === "insertFromPaste" ||
        event.inputType === "insertFromDrop";
      if (!deletes && !inserts) return;
      event.preventDefault();
      const typed = event.data ?? event.dataTransfer?.getData("text/plain") ?? "";
      setQuery(deletes ? "" : typed);
      setActive(null);
      setOpen(true);
    };
    field.addEventListener("beforeinput", startSearch);
    return () => field.removeEventListener("beforeinput", startSearch);
  }, [showsLabel]);

  // A click outside does what Escape does. The focus usually leaves with it and closes
  // the list; a touch on something that takes no focus is heard here.
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && root.current?.contains(event.target)) return;
      setOpen(false);
      setQuery(null);
      setActive(null);
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);

  // The list lies over what follows the field: inside a sheet it is brought into view
  // within the part that scrolls. The chosen label that is put back is selected again,
  // so that typing starts another search.
  useEffect(() => {
    if (open) popup.current?.scrollIntoView({ block: "nearest" });
    else if (document.activeElement === input.current) input.current?.select();
  }, [open, value]);
  useEffect(() => {
    if (activeId !== undefined) {
      document.getElementById(activeId)?.scrollIntoView({ block: "nearest" });
    }
  }, [activeId]);

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    const last = matches.length - 1;
    switch (event.key) {
      case "ArrowDown":
      case "ArrowUp": {
        event.preventDefault();
        setOpen(true);
        if (last < 0) return;
        const down = event.key === "ArrowDown";
        if (activeAt !== null) {
          setActive(down ? Math.min(activeAt + 1, last) : Math.max(activeAt - 1, 0));
          return;
        }
        // Without a query the list starts at the chosen option.
        const chosenAt = query === null ? matches.findIndex((option) => option === chosen) : -1;
        setActive(chosenAt >= 0 ? chosenAt : down ? 0 : last);
        return;
      }
      case "Home":
      case "End":
        if (!open || last < 0) return;
        event.preventDefault();
        setActive(event.key === "Home" ? 0 : last);
        return;
      case "Enter": {
        // With the list closed, Enter is the form's.
        if (!open) return;
        event.preventDefault();
        const option =
          activeAt !== null ? matches[activeAt] : (query ?? "").trim() !== "" ? matches[0] : null;
        if (option != null) choose(option);
        return;
      }
      case "Escape":
        if (open) close();
        return;
      case "Tab":
        close();
        return;
    }
  };

  return (
    <div ref={root} data-slot="picker" className="relative">
      <Input
        {...control}
        ref={input}
        name={name}
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls={listId}
        aria-activedescendant={activeId}
        autoComplete="off"
        autoCapitalize="none"
        autoCorrect="off"
        spellCheck={false}
        disabled={disabled}
        placeholder={placeholder}
        value={query ?? chosen?.label ?? ""}
        onChange={(event) => {
          setQuery(event.target.value);
          setActive(null);
          setOpen(true);
        }}
        onFocus={(event) => event.target.select()}
        onClick={() => setOpen(true)}
        // Letters put together from several keys replace the label as typed ones do.
        onCompositionStart={(event) => {
          if (query === null) event.currentTarget.select();
        }}
        onKeyDown={onKeyDown}
        onBlur={close}
        className="pr-10"
      />
      <ChevronDownIcon
        aria-hidden
        className={cn(
          "pointer-events-none absolute top-1/2 right-3 size-5 -translate-y-1/2",
          disabled ? "text-subtle" : "text-muted-foreground",
        )}
      />
      {open && (
        <div
          ref={popup}
          data-slot="picker-list"
          // The focus stays in the field while an option is clicked.
          onMouseDown={(event) => event.preventDefault()}
          className="absolute inset-x-0 top-full z-10 mt-1 max-h-80 overflow-y-auto rounded-md border border-border-strong bg-card"
        >
          <Options
            id={listId}
            label={label}
            options={matches}
            value={value}
            active={activeAt}
            onActive={setActive}
            onChoose={choose}
            noMatch={noMatch}
          />
        </div>
      )}
      <span role="status" aria-live="polite" className="sr-only">
        {open && query !== null ? resultsText(matches.length, noMatch) : ""}
      </span>
    </div>
  );
}

/**
 * Below 768 px: the field is a button that looks like a field, named by its label and
 * what it shows, and a tap opens a sheet titled by the label. A search field is pinned
 * under the title and the list fills the sheet under it. The sheet takes the focus to
 * itself; a tap in the search field brings the keyboard. A tap on an option chooses it
 * and closes the sheet, and so does Enter for the first match.
 *
 * The button carries the field's `name` and the chosen `value`, as a field does.
 */
function SheetPicker({
  control,
  label,
  name,
  value,
  chosen,
  list,
  onChoose,
  placeholder,
  searchLabel,
  noMatch,
  disabled,
}: PickerProps & { control: ControlProps; searchLabel: string }) {
  const listId = useId();
  const [open, setOpen] = useState(false);
  /** What was typed since the sheet opened, or null before the first keystroke. */
  const [query, setQuery] = useState<string | null>(null);

  const matches = matchOptions(list, query ?? "");
  const shown = chosen?.label ?? placeholder;

  const choose = (option: Option) => {
    onChoose(option);
    setOpen(false);
  };

  return (
    <>
      <button
        {...control}
        type="button"
        name={name}
        value={value}
        disabled={disabled}
        aria-haspopup="dialog"
        aria-label={`${label}: ${shown}`}
        onClick={(event) => {
          // The sheet gives the focus back to what had it: the button, also where a tap
          // leaves the focus elsewhere.
          event.currentTarget.focus();
          setQuery(null);
          setOpen(true);
        }}
        className={cn(
          fieldClasses,
          "flex h-12 items-center gap-2 text-left",
          chosen === null && "text-muted-foreground",
        )}
      >
        <span className="min-w-0 flex-1 truncate">{shown}</span>
        <ChevronDownIcon
          aria-hidden
          className={cn("size-5 shrink-0", disabled ? "text-subtle" : "text-muted-foreground")}
        />
      </button>

      <ResponsiveSheet open={open} onOpenChange={setOpen} title={label}>
        <div data-slot="picker-search" className="shrink-0 px-4 pt-2 pb-3">
          <Input
            type="search"
            enterKeyHint="search"
            autoCapitalize="none"
            autoCorrect="off"
            autoComplete="off"
            spellCheck={false}
            aria-label={searchLabel}
            aria-controls={listId}
            placeholder={searchLabel}
            value={query ?? ""}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key !== "Enter") return;
              event.preventDefault();
              const first = matches[0];
              if (first !== undefined) choose(first);
            }}
          />
        </div>
        {/* As tall as every option together, whatever the query finds: the sheet keeps
            its height and the search field its place while the list gets shorter. */}
        <div
          data-slot="picker-list"
          className="min-h-0 shrink overflow-y-auto"
          style={{ height: `${list.length * 3}rem` }}
        >
          <Options
            id={listId}
            label={label}
            options={matches}
            value={value}
            active={null}
            onChoose={choose}
            noMatch={noMatch}
            sheet
          />
        </div>
        <div aria-hidden className="h-[max(0.5rem,env(safe-area-inset-bottom))] shrink-0" />
        <span role="status" aria-live="polite" className="sr-only">
          {query !== null ? resultsText(matches.length, noMatch) : ""}
        </span>
      </ResponsiveSheet>
    </>
  );
}

/**
 * The list of a picker: a listbox named by the field's label. An option wraps its label
 * and is at least 44 px tall, 48 px in the sheet; the chosen one carries the check and
 * `aria-selected`, the active one the `--raised` fill. With no match the list says so.
 */
function Options({
  id,
  label,
  options,
  value,
  active,
  onActive,
  onChoose,
  noMatch,
  sheet = false,
}: {
  id: string;
  label: string;
  options: readonly Option[];
  value: string;
  active: number | null;
  onActive?: (index: number) => void;
  onChoose: (option: Option) => void;
  noMatch: string;
  sheet?: boolean;
}) {
  return (
    <>
      <ul id={id} role="listbox" aria-label={label}>
        {options.map((option, index) => {
          const chosen = option.value === value;
          return (
            <li
              key={option.value}
              id={`${id}-${index}`}
              role="option"
              aria-selected={chosen}
              onClick={() => onChoose(option)}
              onPointerMove={() => {
                if (index !== active) onActive?.(index);
              }}
              className={cn(
                "flex cursor-pointer items-center gap-3 py-2 type-body",
                sheet ? "min-h-12 px-4 hover:bg-raised active:bg-raised" : "min-h-11 px-3",
                index === active && "bg-raised",
              )}
            >
              <span className="min-w-0 flex-1 wrap-break-word">{option.label}</span>
              {chosen && <CheckIcon aria-hidden className="size-5 shrink-0" />}
            </li>
          );
        })}
      </ul>
      {options.length === 0 && (
        <p className={cn("py-3 type-body-sm text-muted-foreground", sheet ? "px-4" : "px-3")}>
          {noMatch}
        </p>
      )}
    </>
  );
}
