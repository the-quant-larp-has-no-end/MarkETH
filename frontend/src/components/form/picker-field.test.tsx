import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, test, vi } from "vitest";

import { ResponsiveSheet, SheetBody } from "@/components/sheet";
import { setViewport } from "@/test/media";

import { matchOptions, PickerField, type PickerFieldProps } from "./picker-field";

const OPTIONS = [
  { value: "bsc-cs", label: "BSc Computer Science" },
  { value: "bsc-math", label: "BSc Mathematics" },
  { value: "msc-cs", label: "MSc Computer Science" },
  { value: "msc-ds", label: "MSc Data Science" },
  { value: "msc-zh", label: "MSc Zürich Studies" },
] as const;

const LABELS = OPTIONS.map((option) => option.label);

type HarnessProps = Partial<PickerFieldProps> & { initial?: string };

/** The picker with the state a form keeps for it. */
function Picker({ initial = "", onChange, ...props }: HarnessProps) {
  const [value, setValue] = useState(initial);
  return (
    <PickerField
      label="Programme"
      name="degree_programme"
      options={OPTIONS}
      placeholder="Choose a programme"
      searchLabel="Search programmes"
      noMatch="No programme matches."
      {...props}
      value={value}
      onChange={(next) => {
        setValue(next);
        onChange?.(next);
      }}
    />
  );
}

function renderPicker(props: HarnessProps = {}) {
  const onChange = vi.fn();
  const view = render(
    <>
      <Picker onChange={onChange} {...props} />
      <button type="button">After</button>
    </>,
  );
  return { ...view, onChange, user: userEvent.setup() };
}

const combobox = () => screen.getByRole("combobox", { name: "Programme" });
const listbox = () => screen.getByRole("listbox", { name: "Programme" });
const shown = () => screen.getAllByRole("option").map((option) => option.textContent);
const option = (name: string) => screen.getByRole("option", { name });
const active = () => {
  const id = combobox().getAttribute("aria-activedescendant");
  return id === null ? null : (document.getElementById(id)?.textContent ?? null);
};
/** The visually hidden region that says how many options a query found. */
const said = () => screen.getByRole("status");

const field = () => screen.getByLabelText("Programme");
const sheet = () => screen.getByRole("dialog", { name: "Programme" });
const search = () => screen.getByRole("searchbox", { name: "Search programmes" });

/** The bottom sheet takes half a second to slide away before it is gone. */
async function gone(name: string) {
  await waitFor(() => expect(screen.queryByRole("dialog", { name })).not.toBeInTheDocument(), {
    timeout: 3000,
  });
}

describe("the matching rule", () => {
  test("an empty query finds every option, in the order given", () => {
    expect(matchOptions(OPTIONS, "")).toEqual(OPTIONS);
    expect(matchOptions(OPTIONS, "   ")).toEqual(OPTIONS);
  });

  test("case does not count", () => {
    expect(matchOptions(OPTIONS, "DATA").map((found) => found.label)).toEqual(["MSc Data Science"]);
    expect(matchOptions(OPTIONS, "bsc").map((found) => found.label)).toEqual([
      "BSc Computer Science",
      "BSc Mathematics",
    ]);
  });

  test("diacritics are folded, in the query and in the label", () => {
    expect(matchOptions(OPTIONS, "zurich").map((found) => found.label)).toEqual([
      "MSc Zürich Studies",
    ]);
    expect(matchOptions(OPTIONS, "Zürich").map((found) => found.label)).toEqual([
      "MSc Zürich Studies",
    ]);
    expect(matchOptions(OPTIONS, "mäth").map((found) => found.label)).toEqual(["BSc Mathematics"]);
  });

  test("every part of the query has to occur, wherever it stands in the label", () => {
    expect(matchOptions(OPTIONS, "msc data").map((found) => found.label)).toEqual([
      "MSc Data Science",
    ]);
    expect(matchOptions(OPTIONS, "science  msc").map((found) => found.label)).toEqual([
      "MSc Computer Science",
      "MSc Data Science",
    ]);
    expect(matchOptions(OPTIONS, "msc physics")).toEqual([]);
  });

  test("the options keep the order they were given: nothing is ranked", () => {
    const reversed = [...OPTIONS].reverse();

    expect(matchOptions(reversed, "computer").map((found) => found.label)).toEqual([
      "MSc Computer Science",
      "BSc Computer Science",
    ]);
  });
});

describe("from 768 px: a combobox in place", () => {
  test("the field is a combobox named by its label, and its list a listbox of the same name", async () => {
    setViewport("wide");
    const { user } = renderPicker({ initial: "msc-ds" });

    expect(combobox()).toBe(field());
    expect(combobox()).toHaveAttribute("aria-autocomplete", "list");
    expect(combobox()).toHaveAttribute("aria-expanded", "false");
    expect(combobox()).toHaveAttribute("name", "degree_programme");
    expect(combobox()).toHaveValue("MSc Data Science");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();

    await user.click(combobox());

    expect(combobox()).toHaveAttribute("aria-expanded", "true");
    expect(combobox()).toHaveAttribute("aria-controls", listbox().id);
    expect(shown()).toEqual(LABELS);
    expect(option("MSc Data Science")).toHaveAttribute("aria-selected", "true");
    expect(option("BSc Mathematics")).toHaveAttribute("aria-selected", "false");
    // The check stands beside the chosen option, and is not part of its name.
    expect(option("MSc Data Science").querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(option("BSc Mathematics").querySelector("svg")).toBeNull();
  });

  test("while nothing is chosen the field shows the placeholder", () => {
    setViewport("wide");
    renderPicker();

    expect(combobox()).toHaveValue("");
    expect(combobox()).toHaveAttribute("placeholder", "Choose a programme");
  });

  test("focus selects the field's text, and typing filters the list", async () => {
    setViewport("wide");
    const { user } = renderPicker({ initial: "msc-ds" });

    await user.tab();

    const input = combobox() as HTMLInputElement;
    expect(input).toHaveFocus();
    expect([input.selectionStart, input.selectionEnd]).toEqual([0, "MSc Data Science".length]);
    // The Tab key alone opens nothing.
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();

    await user.keyboard("sci");

    expect(input).toHaveValue("sci");
    expect(shown()).toEqual(["BSc Computer Science", "MSc Computer Science", "MSc Data Science"]);

    await user.keyboard(" d");
    expect(shown()).toEqual(["MSc Data Science"]);

    await user.clear(input);
    expect(shown()).toEqual(LABELS);
  });

  test("what is typed over the chosen label starts a search of its own, wherever the caret stands", async () => {
    setViewport("wide");
    const { user, onChange } = renderPicker({ initial: "msc-ds" });
    await user.tab();
    const input = combobox() as HTMLInputElement;
    // A click leaves a caret in the label where the pointer went down.
    input.setSelectionRange(3, 3);
    await user.click(input);
    expect(listbox()).toBeInTheDocument();
    input.setSelectionRange(3, 3);

    await user.keyboard("m");

    expect(input).toHaveValue("m");
    expect(shown()).toEqual([
      "BSc Computer Science",
      "BSc Mathematics",
      "MSc Computer Science",
      "MSc Data Science",
      "MSc Zürich Studies",
    ]);

    await user.keyboard("ath{Enter}");

    expect(onChange).toHaveBeenCalledExactlyOnceWith("bsc-math");
    expect(input).toHaveValue("BSc Mathematics");
  });

  test("a deletion in the chosen label empties the field, and every option is listed", async () => {
    setViewport("wide");
    const { user, onChange } = renderPicker({ initial: "msc-ds" });
    await user.tab();
    const input = combobox() as HTMLInputElement;
    input.setSelectionRange(16, 16);

    await user.keyboard("{Backspace}");

    expect(input).toHaveValue("");
    expect(input).toHaveAttribute("placeholder", "Choose a programme");
    expect(shown()).toEqual(LABELS);
    expect(said()).toHaveTextContent("5 results");

    // What is typed from here is edited as any text is.
    await user.keyboard("mscx{Backspace}");
    expect(input).toHaveValue("msc");
    expect(shown()).toEqual(["MSc Computer Science", "MSc Data Science", "MSc Zürich Studies"]);

    await user.keyboard("{Escape}");
    expect(input).toHaveValue("MSc Data Science");
    expect(onChange).not.toHaveBeenCalled();
  });

  test("after the query changed, a polite region says how many options it found", async () => {
    setViewport("wide");
    const { user } = renderPicker();

    expect(said()).toHaveAttribute("aria-live", "polite");
    expect(said()).toBeEmptyDOMElement();

    await user.type(combobox(), "sc");
    expect(said()).toHaveTextContent("5 results");

    await user.type(combobox(), "{Backspace}{Backspace}math");
    expect(said()).toHaveTextContent("1 result");

    await user.type(combobox(), "x");
    expect(said()).toHaveTextContent("No programme matches.");
    // The list says so too, and offers nothing.
    expect(screen.queryAllByRole("option")).toEqual([]);
    expect(
      within(document.querySelector("[data-slot=picker-list]") as HTMLElement).getByText(
        "No programme matches.",
      ),
    ).toBeInTheDocument();
  });

  test("Arrow Down opens the list and moves the active option, stopping at the last", async () => {
    setViewport("wide");
    const { user } = renderPicker();
    await user.tab();

    await user.keyboard("{ArrowDown}");
    expect(combobox()).toHaveAttribute("aria-expanded", "true");
    expect(active()).toBe("BSc Computer Science");

    await user.keyboard("{ArrowDown}");
    expect(active()).toBe("BSc Mathematics");

    await user.keyboard("{ArrowDown}{ArrowDown}{ArrowDown}{ArrowDown}{ArrowDown}");
    expect(active()).toBe("MSc Zürich Studies");
  });

  test("Arrow Up opens the list at its last option and stops at the first", async () => {
    setViewport("wide");
    const { user } = renderPicker();
    await user.tab();

    await user.keyboard("{ArrowUp}");
    expect(active()).toBe("MSc Zürich Studies");

    await user.keyboard("{ArrowUp}{ArrowUp}{ArrowUp}{ArrowUp}{ArrowUp}{ArrowUp}");
    expect(active()).toBe("BSc Computer Science");
  });

  test("with a choice made, the arrows start from the chosen option", async () => {
    setViewport("wide");
    const { user } = renderPicker({ initial: "msc-cs" });
    await user.tab();

    await user.keyboard("{ArrowDown}");
    expect(active()).toBe("MSc Computer Science");

    await user.keyboard("{ArrowDown}");
    expect(active()).toBe("MSc Data Science");
  });

  test("Home and End take the active option to the first and the last", async () => {
    setViewport("wide");
    const { user } = renderPicker();
    await user.tab();
    await user.keyboard("{ArrowDown}{ArrowDown}");

    await user.keyboard("{End}");
    expect(active()).toBe("MSc Zürich Studies");

    await user.keyboard("{Home}");
    expect(active()).toBe("BSc Computer Science");
  });

  test("Enter chooses the active option, closes the list and leaves the focus in the field", async () => {
    setViewport("wide");
    const { user, onChange } = renderPicker();
    await user.tab();

    await user.keyboard("{ArrowDown}{ArrowDown}{Enter}");

    expect(onChange).toHaveBeenCalledExactlyOnceWith("bsc-math");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(combobox()).toHaveAttribute("aria-expanded", "false");
    expect(combobox()).toHaveValue("BSc Mathematics");
    expect(combobox()).toHaveFocus();
  });

  test("Enter chooses the first match of a query while no option is active", async () => {
    setViewport("wide");
    const { user, onChange } = renderPicker();

    await user.type(combobox(), "msc{Enter}");

    expect(onChange).toHaveBeenCalledExactlyOnceWith("msc-cs");
    expect(combobox()).toHaveValue("MSc Computer Science");
  });

  test("Enter chooses nothing while the query is empty and no option is active, or finds nothing", async () => {
    setViewport("wide");
    const { user, onChange } = renderPicker();

    await user.click(combobox());
    await user.keyboard("{Enter}");
    expect(listbox()).toBeInTheDocument();

    await user.keyboard("xyz{Enter}");
    expect(onChange).not.toHaveBeenCalled();
  });

  test("Escape closes the list and puts the chosen label back", async () => {
    setViewport("wide");
    const { user, onChange } = renderPicker({ initial: "msc-ds" });

    await user.type(combobox(), "bsc");
    expect(combobox()).toHaveValue("bsc");

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(combobox()).toHaveValue("MSc Data Science");
    expect(combobox()).toHaveFocus();
    expect(onChange).not.toHaveBeenCalled();
    expect(said()).toBeEmptyDOMElement();
  });

  test("Tab closes the list, puts the chosen label back and moves on, choosing nothing", async () => {
    setViewport("wide");
    const { user, onChange } = renderPicker({ initial: "msc-ds" });

    await user.type(combobox(), "bsc");
    await user.keyboard("{ArrowDown}");
    expect(active()).toBe("BSc Computer Science");

    await user.tab();

    expect(screen.getByRole("button", { name: "After" })).toHaveFocus();
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(combobox()).toHaveValue("MSc Data Science");
    expect(onChange).not.toHaveBeenCalled();
  });

  test("a click on an option chooses it, and the focus stays in the field", async () => {
    setViewport("wide");
    const { user, onChange } = renderPicker();

    await user.click(combobox());
    await user.click(option("MSc Data Science"));

    expect(onChange).toHaveBeenCalledExactlyOnceWith("msc-ds");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(combobox()).toHaveValue("MSc Data Science");
    expect(combobox()).toHaveFocus();
  });

  test("the option under the pointer is the active one", async () => {
    setViewport("wide");
    const { user } = renderPicker();

    await user.click(combobox());
    await user.hover(option("MSc Data Science"));

    expect(active()).toBe("MSc Data Science");
  });

  test("choosing the option that is chosen already changes nothing", async () => {
    setViewport("wide");
    const { user, onChange } = renderPicker({ initial: "msc-ds" });

    await user.click(combobox());
    await user.click(option("MSc Data Science"));

    expect(onChange).not.toHaveBeenCalled();
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(combobox()).toHaveValue("MSc Data Science");
  });

  test("a click outside does what Escape does", async () => {
    setViewport("wide");
    const { user, onChange } = renderPicker({ initial: "msc-ds" });

    await user.type(combobox(), "bsc");
    await user.click(document.body);

    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(combobox()).toHaveValue("MSc Data Science");
    expect(onChange).not.toHaveBeenCalled();
  });

  test("a value that is not among the options is shown as its own text and listed first, as chosen", async () => {
    setViewport("wide");
    const { user } = renderPicker({ initial: "MSc Astrophysics" });

    expect(combobox()).toHaveValue("MSc Astrophysics");

    await user.click(combobox());

    expect(shown()).toEqual(["MSc Astrophysics", ...LABELS]);
    expect(option("MSc Astrophysics")).toHaveAttribute("aria-selected", "true");
  });

  test("with `any`, the first option stands for no filter and is the chosen one while nothing is set", async () => {
    setViewport("wide");
    const { user, onChange } = renderPicker({ any: "Any programme" });

    expect(combobox()).toHaveValue("Any programme");

    await user.click(combobox());
    expect(shown()).toEqual(["Any programme", ...LABELS]);
    expect(option("Any programme")).toHaveAttribute("aria-selected", "true");

    await user.click(option("MSc Data Science"));
    expect(onChange).toHaveBeenLastCalledWith("msc-ds");

    await user.click(combobox());
    await user.click(option("Any programme"));
    expect(onChange).toHaveBeenLastCalledWith("");
    expect(combobox()).toHaveValue("Any programme");
  });

  test("a disabled picker opens nothing", async () => {
    setViewport("wide");
    const { user } = renderPicker({ disabled: true });

    expect(combobox()).toBeDisabled();
    await user.click(combobox());

    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  test("help text and an error stand under the field and describe it", () => {
    setViewport("wide");
    renderPicker({ help: "Your degree at ETH.", error: "This programme is not offered." });

    expect(combobox()).toBeInvalid();
    expect(combobox()).toHaveAccessibleDescription(
      "This programme is not offered. Your degree at ETH.",
    );
  });
});

describe("below 768 px: a sheet", () => {
  test("the field is a button named by its label and what it shows", async () => {
    const { user } = renderPicker();

    expect(field().tagName).toBe("BUTTON");
    expect(field()).toHaveAccessibleName("Programme: Choose a programme");
    expect(field()).toHaveAttribute("aria-haspopup", "dialog");
    expect(field()).toHaveAttribute("name", "degree_programme");
    expect(field()).toHaveValue("");
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();

    await user.click(field());
    await user.click(await screen.findByRole("option", { name: "MSc Data Science" }));
    await gone("Programme");

    expect(field()).toHaveAccessibleName("Programme: MSc Data Science");
    expect(field()).toHaveValue("msc-ds");
  });

  test("a tap opens a sheet with the label as its title, the close cross, a search field and the list", async () => {
    const { user } = renderPicker({ initial: "msc-ds" });

    await user.click(field());

    expect(await screen.findByRole("dialog", { name: "Programme" })).toHaveAttribute(
      "data-slot",
      "drawer-content",
    );
    expect(within(sheet()).getByRole("heading", { name: "Programme" })).toBeInTheDocument();
    expect(within(sheet()).getByRole("button", { name: "Close" })).toBeEnabled();
    expect(search()).toHaveAttribute("type", "search");
    expect(search()).toHaveAttribute("enterkeyhint", "search");
    expect(search()).toHaveAttribute("autocapitalize", "none");
    expect(search()).toHaveAttribute("autocorrect", "off");
    expect(within(sheet()).getByRole("listbox", { name: "Programme" })).toBeInTheDocument();
    expect(shown()).toEqual(LABELS);
    expect(option("MSc Data Science")).toHaveAttribute("aria-selected", "true");
  });

  test("the sheet takes the focus itself, not the search field: no keyboard slides up over it", async () => {
    const { user } = renderPicker();

    await user.click(field());
    await screen.findByRole("dialog", { name: "Programme" });

    await waitFor(() => expect(sheet()).toHaveFocus());
    expect(search()).not.toHaveFocus();
  });

  test("a tap on an option chooses it, closes the sheet and returns the focus to the field", async () => {
    const { user, onChange } = renderPicker();

    await user.click(field());
    await user.click(await screen.findByRole("option", { name: "BSc Mathematics" }));

    expect(onChange).toHaveBeenCalledExactlyOnceWith("bsc-math");
    await gone("Programme");
    await waitFor(() => expect(field()).toHaveFocus());
  });

  test("typing in the search field filters, the region says how many, and Enter chooses the first match", async () => {
    const { user, onChange } = renderPicker();

    await user.click(field());
    await screen.findByRole("dialog", { name: "Programme" });
    expect(within(sheet()).getByRole("status")).toBeEmptyDOMElement();

    await user.type(search(), "msc");
    expect(shown()).toEqual(["MSc Computer Science", "MSc Data Science", "MSc Zürich Studies"]);
    expect(within(sheet()).getByRole("status")).toHaveTextContent("3 results");

    await user.type(search(), " zurich");
    expect(shown()).toEqual(["MSc Zürich Studies"]);
    expect(within(sheet()).getByRole("status")).toHaveTextContent("1 result");

    await user.keyboard("{Enter}");

    expect(onChange).toHaveBeenCalledExactlyOnceWith("msc-zh");
    await gone("Programme");
  });

  test("with no match the list says so, and Enter chooses nothing", async () => {
    const { user, onChange } = renderPicker();

    await user.click(field());
    await screen.findByRole("dialog", { name: "Programme" });
    await user.type(search(), "law{Enter}");

    expect(screen.queryAllByRole("option")).toEqual([]);
    expect(within(sheet()).getByRole("status")).toHaveTextContent("No programme matches.");
    expect(within(sheet()).getAllByText("No programme matches.")).toHaveLength(2);
    expect(onChange).not.toHaveBeenCalled();
    expect(sheet()).toBeInTheDocument();
  });

  test("every opening starts with an empty search field", async () => {
    const { user } = renderPicker();

    await user.click(field());
    await screen.findByRole("dialog", { name: "Programme" });
    await user.type(search(), "msc");
    await user.keyboard("{Escape}");
    await gone("Programme");

    await user.click(field());
    await screen.findByRole("dialog", { name: "Programme" });

    expect(search()).toHaveValue("");
    expect(shown()).toEqual(LABELS);
  });

  test("a value that is not listed, and `any`: the button shows them as the combobox does", () => {
    const { unmount } = renderPicker({ initial: "MSc Astrophysics" });
    expect(field()).toHaveAccessibleName("Programme: MSc Astrophysics");
    unmount();

    renderPicker({ any: "Any programme" });
    expect(field()).toHaveAccessibleName("Programme: Any programme");
  });

  test("a disabled picker opens nothing", async () => {
    const { user } = renderPicker({ disabled: true });

    expect(field()).toBeDisabled();
    await user.click(field());

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  test("an error marks the button and describes it", () => {
    renderPicker({ error: "This programme is not offered." });

    expect(field()).toBeInvalid();
    expect(field()).toHaveAccessibleDescription("This programme is not offered.");
  });

  test("inside another sheet the picker's sheet is nested: choosing closes it and leaves the sheet under it open", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(
      <ResponsiveSheet open onOpenChange={() => {}} title="Filter">
        <SheetBody>
          <Picker onChange={onChange} any="Any programme" />
        </SheetBody>
      </ResponsiveSheet>,
    );
    const filter = await screen.findByRole("dialog", { name: "Filter" });

    await user.click(within(filter).getByLabelText("Programme"));
    await user.click(await screen.findByRole("option", { name: "MSc Data Science" }));

    expect(onChange).toHaveBeenCalledExactlyOnceWith("msc-ds");
    await gone("Programme");
    expect(screen.getByRole("dialog", { name: "Filter" })).toBeInTheDocument();
    await waitFor(() =>
      expect(
        within(screen.getByRole("dialog", { name: "Filter" })).getByLabelText("Programme"),
      ).toHaveFocus(),
    );
  });

  test("Escape closes the picker's sheet alone", async () => {
    const user = userEvent.setup();
    render(
      <ResponsiveSheet open onOpenChange={() => {}} title="Filter">
        <SheetBody>
          <Picker />
        </SheetBody>
      </ResponsiveSheet>,
    );
    const filter = await screen.findByRole("dialog", { name: "Filter" });

    await user.click(within(filter).getByLabelText("Programme"));
    await screen.findByRole("dialog", { name: "Programme" });
    await user.keyboard("{Escape}");

    await gone("Programme");
    expect(screen.getByRole("dialog", { name: "Filter" })).toBeInTheDocument();
  });
});

describe("crossing 768 px while the list is open", () => {
  test("the sheet closes and the choice is kept", async () => {
    const { user } = renderPicker({ initial: "msc-ds" });
    await user.click(field());
    await screen.findByRole("dialog", { name: "Programme" });

    act(() => setViewport("wide"));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(combobox()).toHaveValue("MSc Data Science");
    expect(combobox()).toHaveAttribute("aria-expanded", "false");
  });

  test("the list in place closes and the choice is kept", async () => {
    setViewport("wide");
    const { user } = renderPicker({ initial: "msc-ds" });
    await user.type(combobox(), "bsc");

    act(() => setViewport("phone"));

    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(field()).toHaveAccessibleName("Programme: MSc Data Science");
  });
});
