import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { MINUS } from "@/lib/format";

import { Amount, SignedAmount } from "./amount";
import { CodeText } from "./code-text";

const amount = () => document.querySelector("[data-slot=amount]")!;
const icon = () => amount().querySelector("[data-slot=token-icon]");

describe("Amount", () => {
  test("shows the token icon, then the string as the API sent it, and no word", () => {
    render(<Amount value="25" />);

    expect(icon()).toBeInTheDocument();
    expect(icon()).toHaveAttribute("aria-hidden", "true");
    expect(icon()?.querySelector("img")).toHaveAttribute("alt", "");
    expect(screen.getByText("25")).toBeInTheDocument();
    // The icon comes before the figure.
    expect(amount().firstElementChild).toBe(icon());
  });

  test("is read out as the number and the word tokens", () => {
    render(<Amount value="25" />);

    expect(screen.getByText("25 tokens")).toHaveClass("sr-only");
    expect(screen.getByText("25")).toHaveAttribute("aria-hidden", "true");
  });

  test("says token for exactly 1", () => {
    render(<Amount value="1" />);

    expect(screen.getByText("1 token")).toBeInTheDocument();
  });

  test("groups thousands and cuts after the second decimal without rounding", () => {
    render(<Amount value="1250.999999" />);

    expect(screen.getByText("1,250.99")).toBeInTheDocument();
    expect(screen.queryByText("1,251")).not.toBeInTheDocument();
  });

  test("shows the whole string where a detail view asks for it", () => {
    render(<Amount value="45.123456" full />);

    expect(screen.getByText("45.123456")).toBeInTheDocument();
    expect(screen.getByText("45.123456 tokens")).toBeInTheDocument();
  });

  test("keeps the minus of a figure the API sends with one, before the icon", () => {
    render(<Amount value="-411.4" />);

    expect(amount().firstElementChild).toHaveTextContent(MINUS);
    expect(screen.getByText("411.4")).toBeInTheDocument();
    expect(screen.getByText(`${MINUS}411.4 tokens`)).toBeInTheDocument();
  });

  test("takes its text style and its icon size from the size", () => {
    const { rerender } = render(<Amount value="240" size="display" />);
    expect(amount()).toHaveClass("type-display");
    expect(icon()).toHaveClass("size-8");

    rerender(<Amount value="240" size="lg" />);
    expect(amount()).toHaveClass("type-number-lg");
    expect(icon()).toHaveClass("size-5");

    rerender(<Amount value="240" />);
    expect(amount()).toHaveClass("type-number");
    expect(icon()).toHaveClass("size-4");
  });
});

describe("SignedAmount", () => {
  test("tokens in carry a plus and the green text tint", () => {
    render(<SignedAmount value="200" />);

    expect(amount().firstElementChild).toHaveTextContent("+");
    expect(amount()).toHaveClass("text-yes-text");
    expect(screen.getByText("+200 tokens")).toBeInTheDocument();
  });

  test("tokens out carry a minus and stay white: lowering the balance is normal", () => {
    render(<SignedAmount value="-25" />);

    expect(amount().firstElementChild).toHaveTextContent(MINUS);
    expect(amount()).toHaveClass("text-foreground");
    expect(amount()).not.toHaveClass("text-no-text");
    expect(screen.getByText(`${MINUS}25 tokens`)).toBeInTheDocument();
  });

  test("the sign stands before the icon", () => {
    render(<SignedAmount value="-25" />);

    const [sign, token] = Array.from(amount().children);
    expect(sign).toHaveTextContent(MINUS);
    expect(token).toBe(icon());
  });
});

describe("CodeText", () => {
  test("shows a code in groups of four, in the code style", () => {
    render(<CodeText value="4F7KQ2M9XAB3" />);

    expect(screen.getByText("4F7K-Q2M9-XAB3")).toHaveClass("type-code");
  });
});
