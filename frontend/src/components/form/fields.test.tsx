import { screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, test, vi } from "vitest";

import { config } from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";

import { CodeInput, DateTimeField, SelectField, TextAreaField, TextField } from "./fields";
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
