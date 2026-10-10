import { screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, test, vi } from "vitest";

import { config } from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";

import { Link } from "@/components/shell";

import { CheckboxField } from "./checkbox-field";
import {
  AmountField,
  CodeInput,
  DateTimeField,
  SelectField,
  TextAreaField,
  TextField,
} from "./fields";
import { revealFirstInvalid } from "./reveal-invalid";

describe("TextField", () => {
  test("has its label above it and its help text as its description", () => {
    renderWithProviders(
      <TextField label="Question" help="10 to 200 characters" value="" onChange={() => {}} />,
    );

    const field = screen.getByLabelText("Question");
    expect(field).toHaveAccessibleDescription("10 to 200 characters");
    expect(field).not.toBeInvalid();
    expect(field).toHaveClass("h-12", "type-body", "bg-background", "border-border-strong");
  });

  test("with an error it is invalid, and the error is announced and named by the field", () => {
    renderWithProviders(
      <TextField
        label="Question"
        help="10 to 200 characters"
        error="String should have at least 10 characters"
        value="Too short"
        onChange={() => {}}
      />,
    );

    const field = screen.getByLabelText("Question");
    expect(field).toBeInvalid();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "String should have at least 10 characters",
    );
    expect(field).toHaveAccessibleDescription(
      "String should have at least 10 characters 10 to 200 characters",
    );
  });

  test("can show how much of its limit is used", () => {
    renderWithProviders(
      <TextField
        label="Question"
        value="Will it rain?"
        maxLength={200}
        showCount
        onChange={() => {}}
      />,
    );

    expect(screen.getByText("13 / 200")).toBeInTheDocument();
  });
});

describe("TextAreaField", () => {
  test("is a field of several lines with the same wiring", () => {
    renderWithProviders(
      <TextAreaField label="How it is decided" error="Too long" value="x" onChange={() => {}} />,
    );

    const field = screen.getByLabelText("How it is decided");
    expect(field.tagName).toBe("TEXTAREA");
    expect(field).toBeInvalid();
  });
});

describe("SelectField", () => {
  const options = [
    { value: "BACHELOR", label: "Bachelor" },
    { value: "MASTER", label: "Master" },
  ];

  test("is the browser's own select, with a placeholder that cannot be chosen", async () => {
    const onChange = vi.fn();
    const { user } = renderWithProviders(
      <SelectField
        label="Level"
        value=""
        onChange={onChange}
        options={options}
        placeholder="Choose a level"
      />,
    );

    const select = screen.getByLabelText("Level");
    expect(select.tagName).toBe("SELECT");
    expect(screen.getByRole("option", { name: "Choose a level" })).toBeDisabled();

    await user.selectOptions(select, "Master");
    expect(onChange).toHaveBeenCalledWith("MASTER");
  });

  test("can be disabled until the field it depends on is set", () => {
    renderWithProviders(
      <SelectField
        label="Programme"
        value=""
        onChange={() => {}}
        options={[]}
        placeholder="Choose a programme"
        disabled
      />,
    );

    expect(screen.getByLabelText("Programme")).toBeDisabled();
  });
});

describe("DateTimeField", () => {
  function Field({ onValue }: { onValue: (value: string | null) => void }) {
    const [value, setValue] = useState<string | null>("2026-10-14T16:00:00Z");
    return (
      <DateTimeField
        label="Trading closes"
        value={value}
        onChange={(next) => {
          setValue(next);
          onValue(next);
        }}
      />
    );
  }

  test("shows an instant as the wall time of the configured timezone, whatever the device is set to", () => {
    renderWithProviders(<Field onValue={() => {}} />);

    expect(screen.getByLabelText("Trading closes")).toHaveValue("2026-10-14T18:00");
  });

  test("hands on what is entered as ISO 8601 with the configured zone's offset", async () => {
    const onValue = vi.fn();
    const { user } = renderWithProviders(<Field onValue={onValue} />);
    const field = screen.getByLabelText("Trading closes");

    await user.clear(field);
    expect(onValue).toHaveBeenLastCalledWith(null);

    await user.type(field, "2026-12-01T17:30");
    expect(onValue).toHaveBeenLastCalledWith("2026-12-01T17:30:00+01:00");
  });

  test("follows the timezone of /config", () => {
    renderWithProviders(<Field onValue={() => {}} />, {
      config: config({ timezone: "Asia/Tokyo" }),
    });

    expect(screen.getByLabelText("Trading closes")).toHaveValue("2026-10-15T01:00");
  });

  test("without a value it has the colour of a placeholder: what stands in it is the browser's own", async () => {
    const { user } = renderWithProviders(<Field onValue={() => {}} />);
    const field = screen.getByLabelText("Trading closes");

    expect(field).toHaveClass("text-foreground");
    expect(field).not.toHaveClass("text-muted-foreground");

    await user.clear(field);
    expect(field).toHaveValue("");
    expect(field).toHaveClass("text-muted-foreground");
    expect(field).not.toHaveClass("text-foreground");

    await user.type(field, "2026-12-01T17:30");
    expect(field).toHaveClass("text-foreground");
    expect(field).not.toHaveClass("text-muted-foreground");
  });
});

describe("CodeInput", () => {
  function Field({ length }: { length: number }) {
    const [value, setValue] = useState("");
    return <CodeInput label="Code" value={value} onChange={setValue} length={length} />;
  }

  test("inserts the dashes and capitals as the user types", async () => {
    const { user } = renderWithProviders(<Field length={12} />);
    const field = screen.getByLabelText("Code");

    await user.type(field, "4f7kq2m9xab3");

    expect(field).toHaveValue("4F7K-Q2M9-XAB3");
    expect(field).toHaveClass("type-code");
  });

  test("takes no more characters than the code is long", async () => {
    const { user } = renderWithProviders(<Field length={8} />);
    const field = screen.getByLabelText("Code");

    await user.type(field, "7kq2m9xazzzz");

    expect(field).toHaveValue("7KQ2-M9XA");
  });

  test("shows the shape of a code of the configured length", () => {
    renderWithProviders(<Field length={12} />);

    expect(screen.getByLabelText("Code")).toHaveAttribute("placeholder", "XXXX-XXXX-XXXX");
    expect(screen.getByLabelText("Code")).toHaveAttribute("autocapitalize", "characters");
    expect(screen.getByLabelText("Code")).toHaveAttribute("autocorrect", "off");
  });

  test("takes a pasted code whole", async () => {
    const { user } = renderWithProviders(<Field length={12} />);
    const field = screen.getByLabelText("Code");

    await user.click(field);
    await user.paste(" 4f7k q2m9 xab3 ");

    expect(field).toHaveValue("4F7K-Q2M9-XAB3");
  });
});

describe("a refused field", () => {
  const refused = { label: "Field", error: "The API's sentence.", onChange: () => {} };
  const kinds = [
    ["a text field", <TextField key="text" {...refused} value="x" />],
    ["a field of several lines", <TextAreaField key="area" {...refused} value="x" />],
    [
      "a select",
      <SelectField
        key="select"
        {...refused}
        value="MASTER"
        options={[{ value: "MASTER", label: "Master" }]}
        placeholder="Choose a level"
      />,
    ],
    ["a date and time", <DateTimeField key="date" {...refused} value="2026-10-14T16:00:00Z" />],
    ["an amount", <AmountField key="amount" {...refused} value="150" />],
    ["a code", <CodeInput key="code" {...refused} value="4F7K" length={12} />],
  ] as const;

  test.each(kinds)(
    "%s has its border in the error colour, and keeps it while it has the focus",
    (_kind, field) => {
      renderWithProviders(field);

      const control = screen.getByLabelText("Field");
      expect(control).toBeInvalid();
      // One rule for every kind: they share the classes of a field.
      expect(control).toHaveClass(
        "aria-invalid:border-no",
        "aria-invalid:focus-visible:border-no",
        "aria-invalid:focus-visible:outline-no",
      );
      // Without a refusal the focus is the white ring, which the same classes hold.
      expect(control).toHaveClass("focus-visible:border-ring", "focus-visible:outline-ring");
    },
  );
});

describe("revealFirstInvalid", () => {
  test("brings the first field the API refused into view and gives it the focus", () => {
    const intoView = vi.spyOn(Element.prototype, "scrollIntoView");
    renderWithProviders(
      <form data-testid="form">
        <TextField label="Question" value="" onChange={() => {}} />
        <TextField label="Source" value="" onChange={() => {}} error="Name a source." />
        <TextField label="Label" value="" onChange={() => {}} error="Too long." />
      </form>,
    );

    revealFirstInvalid(screen.getByTestId("form"));

    const refused = screen.getByLabelText("Source");
    expect(refused).toHaveFocus();
    expect(intoView).toHaveBeenCalledTimes(1);
    expect(intoView.mock.contexts[0]).toBe(refused);
  });

  test("does nothing where nothing was refused, and nothing without a form", () => {
    const intoView = vi.spyOn(Element.prototype, "scrollIntoView");
    renderWithProviders(
      <form data-testid="form">
        <TextField label="Question" value="" onChange={() => {}} />
      </form>,
    );

    revealFirstInvalid(screen.getByTestId("form"));
    revealFirstInvalid(null);

    expect(intoView).not.toHaveBeenCalled();
    expect(document.body).toHaveFocus();
  });
});

describe("AmountField", () => {
  function Field({ whole, onValue }: { whole?: boolean; onValue?: (value: string) => void }) {
    const [value, setValue] = useState("");
    return (
      <AmountField
        label="Amount"
        help="Tokens to add."
        value={value}
        whole={whole}
        onChange={(next) => {
          setValue(next);
          onValue?.(next);
        }}
      />
    );
  }

  test("is a text field that opens the decimal keypad, wired like every field", () => {
    renderWithProviders(<Field />);

    const field = screen.getByLabelText("Amount");
    expect(field).toHaveAttribute("type", "text");
    expect(field).toHaveAttribute("inputmode", "decimal");
    expect(field).toHaveAttribute("autocomplete", "off");
    expect(field).toHaveAccessibleDescription("Tokens to add.");
    expect(field).toHaveClass("h-12", "type-body");
  });

  test("takes digits and one decimal point; a comma becomes a point and nothing else is taken", async () => {
    const onValue = vi.fn();
    const { user } = renderWithProviders(<Field onValue={onValue} />);
    const field = screen.getByLabelText("Amount");

    await user.type(field, "1a2,5.0 -x7");

    // What the field shows is what is handed on, and what is sent.
    expect(field).toHaveValue("12.507");
    expect(onValue).toHaveBeenLastCalledWith("12.507");
  });

  test("keeps what was typed as it was typed: nothing is completed, rounded or read as a number", async () => {
    const { user } = renderWithProviders(<Field />);
    const field = screen.getByLabelText("Amount");

    await user.click(field);
    await user.paste("007.500000000000000000000001");

    expect(field).toHaveValue("007.500000000000000000000001");
  });

  test("a whole number is digits alone, on the numeric keypad", async () => {
    const { user } = renderWithProviders(<Field whole />);
    const field = screen.getByLabelText("Amount");

    await user.type(field, "2 0.5,k0");

    expect(field).toHaveAttribute("inputmode", "numeric");
    expect(field).toHaveValue("2050");
  });

  test("an error of the API stands under it", () => {
    renderWithProviders(
      <AmountField
        label="Amount"
        value="0"
        onChange={() => {}}
        error="An adjustment cannot be zero."
      />,
    );

    expect(screen.getByLabelText("Amount")).toBeInvalid();
    expect(screen.getByRole("alert")).toHaveTextContent("An adjustment cannot be zero.");
  });
});

describe("CheckboxField", () => {
  function Field({ error, disabled }: { error?: string; disabled?: boolean }) {
    const [checked, setChecked] = useState(false);
    return (
      <form data-testid="form">
        <CheckboxField
          name="policy_accepted"
          checked={checked}
          onChange={setChecked}
          error={error}
          disabled={disabled}
        >
          I accept the <Link href="/policy">usage policy</Link>
        </CheckboxField>
      </form>
    );
  }

  const box = () => screen.getByRole("checkbox", { name: "I accept the usage policy" });

  test("is named by its whole text, and starts as it is told", () => {
    renderWithProviders(<Field />);

    expect(box()).not.toBeChecked();
    expect(box()).not.toBeInvalid();
  });

  test("a 20 px box on the page's ground, in a row at least 44 px tall", () => {
    renderWithProviders(<Field />);

    expect(box()).toHaveClass("size-5", "rounded-sm", "border-border-strong", "bg-background");
    expect(box()).toHaveClass("data-[state=checked]:bg-primary");
    expect(box().closest("label")).toHaveClass("min-h-11", "cursor-pointer");
  });

  test("a tap on the box or on the text ticks it, and another unticks it", async () => {
    const { user } = renderWithProviders(<Field />);

    await user.click(screen.getByText("I accept the"));
    expect(box()).toBeChecked();

    await user.click(box());
    expect(box()).not.toBeChecked();
  });

  test("a link inside the text is a target of its own: it opens what it names and leaves the box as it is", async () => {
    const { user } = renderWithProviders(<Field />);
    const link = screen.getByRole("link", { name: "usage policy" });

    await user.click(link);

    expect(box()).not.toBeChecked();
    expect(link).toHaveAttribute("href", "/policy");
  });

  test("the keyboard reaches the box and the link as two stops, and Space ticks the box", async () => {
    const { user } = renderWithProviders(<Field />);

    await user.tab();
    expect(box()).toHaveFocus();
    await user.keyboard(" ");
    expect(box()).toBeChecked();

    await user.tab();
    expect(screen.getByRole("link", { name: "usage policy" })).toHaveFocus();
  });

  test("an error of the API stands below the row, describes the box and is found as the refused field", () => {
    renderWithProviders(<Field error="Accept the usage policy to set up your profile." />);

    expect(box()).toBeInvalid();
    expect(box()).toHaveAccessibleDescription("Accept the usage policy to set up your profile.");
    const error = screen.getByRole("alert");
    expect(error).toHaveTextContent("Accept the usage policy to set up your profile.");
    // Below the row, not inside it: it is no part of the checkbox's name.
    expect(box().closest("label")).not.toContainElement(error);
    expect(box().closest("label")?.nextElementSibling).toBe(error);

    revealFirstInvalid(screen.getByTestId("form"));
    expect(box()).toHaveFocus();
  });

  test("can be switched off", async () => {
    const { user } = renderWithProviders(<Field disabled />);

    await user.click(screen.getByText("I accept the"));

    expect(box()).toBeDisabled();
    expect(box()).not.toBeChecked();
  });
});
