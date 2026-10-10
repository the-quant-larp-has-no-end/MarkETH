import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";

import { Button } from "./button";

describe("Button", () => {
  test("is the primary button by default: 48 px, the white fill, the navy label, full width on phones", () => {
    render(<Button>Confirm</Button>);

    const button = screen.getByRole("button", { name: "Confirm" });
    expect(button).toHaveClass(
      "h-12",
      "rounded-md",
      "type-label",
      "bg-primary",
      "text-primary-foreground",
      "w-full",
      "md:w-auto",
    );
  });

  test.each([
    ["secondary", ["border-border-strong", "text-foreground"]],
    ["ghost", ["text-foreground"]],
    ["destructive", ["bg-no", "text-ink"]],
    ["yes", ["bg-yes", "text-ink"]],
    ["no", ["bg-no", "text-ink"]],
  ] as const)("the %s variant", (variant, classes) => {
    render(<Button variant={variant}>Action</Button>);

    expect(screen.getByRole("button")).toHaveClass(...classes);
  });

  test("labels on green and red are ink, never white", () => {
    for (const variant of ["yes", "no", "destructive"] as const) {
      const { unmount } = render(<Button variant={variant}>Action</Button>);
      expect(screen.getByRole("button").className).not.toMatch(/text-(foreground|primary)\b/);
      unmount();
    }
  });

  test("the Confirm of the trade sheet is 56 px, and an icon button is a 44 px square", () => {
    const { rerender } = render(<Button size="confirm">Buy YES</Button>);
    expect(screen.getByRole("button")).toHaveClass("h-14");

    rerender(
      <Button size="icon" aria-label="Close">
        x
      </Button>,
    );
    expect(screen.getByRole("button")).toHaveClass("size-11");
    expect(screen.getByRole("button")).not.toHaveClass("w-full");
  });

  test("does not submit a form unless it says so", () => {
    const onSubmit = vi.fn((event: { preventDefault(): void }) => event.preventDefault());
    render(
      <form onSubmit={onSubmit}>
        <Button>Cancel</Button>
        <Button type="submit">Save</Button>
      </form>,
    );

    expect(screen.getByRole("button", { name: "Cancel" })).toHaveAttribute("type", "button");
    screen.getByRole("button", { name: "Cancel" }).click();
    expect(onSubmit).not.toHaveBeenCalled();

    screen.getByRole("button", { name: "Save" }).click();
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  test("while loading it keeps its label for its width and its name, shows a spinner and cannot be tapped", async () => {
    const onClick = vi.fn();
    render(
      <Button loading onClick={onClick}>
        Redeem
      </Button>,
    );

    const button = screen.getByRole("button", { name: "Redeem" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(button.querySelector("svg")).toHaveClass("animate-spin");
    expect(screen.getByText("Redeem")).toHaveClass("opacity-0");

    await userEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });

  test("can lend its look to a link", () => {
    render(
      <Button variant="secondary" asChild>
        <a href="/propose">Propose a market</a>
      </Button>,
    );

    const link = screen.getByRole("link", { name: "Propose a market" });
    expect(link).toHaveClass("h-12", "border-border-strong");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
