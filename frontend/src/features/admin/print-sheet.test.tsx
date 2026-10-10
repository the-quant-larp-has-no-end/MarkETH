import { within } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import { codeBatch } from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";

import { PrintSheet } from "./print-sheet";

/* What a QR holds cannot be read off its picture: the test looks at what it was given. */
vi.mock("qrcode.react", () => ({
  QRCodeSVG: ({
    value,
    marginSize,
    className,
  }: {
    value: string;
    marginSize?: number;
    className?: string;
  }) => <svg data-value={value} data-margin={marginSize} className={className} />,
}));

const codes = (count: number) =>
  Array.from({ length: count }, (_, index) => ({
    code: `CODE${String(index).padStart(8, "0")}`,
    redeem_url: `http://localhost:8080/wallet?code=CODE${String(index).padStart(8, "0")}`,
  }));

const sheet = (container: HTMLElement) =>
  container.querySelector<HTMLElement>("[data-slot=print-sheet]")!;
const rows = (container: HTMLElement) => [
  ...container.querySelectorAll<HTMLElement>("[data-slot=print-row]"),
];
const cardsOf = (row: HTMLElement) => [
  ...row.querySelectorAll<HTMLElement>("[data-slot=print-card]"),
];

describe("the printable QR sheet", () => {
  test("lays the codes out three to a row, in their order, and the last row holds what is left", () => {
    const { container } = renderWithProviders(<PrintSheet batch={codeBatch()} codes={codes(7)} />);

    expect(rows(container).map((row) => cardsOf(row).length)).toEqual([3, 3, 1]);
    expect(
      [...container.querySelectorAll("[data-slot=print-card] svg")].map((qr) =>
        qr.getAttribute("data-value"),
      ),
    ).toEqual(codes(7).map((code) => code.redeem_url));
    // Three columns of 58 mm, 6 mm apart, on a sheet as wide as A4 leaves between 12 mm margins.
    for (const row of rows(container)) {
      expect(row).toHaveClass("grid-cols-[repeat(3,58mm)]", "gap-[6mm]");
    }
    expect(sheet(container)).toHaveClass("w-[186mm]");
  });

  test.each([1, 2])(
    "a batch of %i keeps the three columns: its cards are not stretched across the page",
    (count) => {
      const { container } = renderWithProviders(
        <PrintSheet batch={codeBatch()} codes={codes(count)} />,
      );

      const [only] = rows(container);
      expect(cardsOf(only!)).toHaveLength(count);
      expect(only).toHaveClass("grid-cols-[repeat(3,58mm)]");
      // The sheet keeps its width whatever it holds, and so do its columns.
      expect(sheet(container)).not.toHaveClass("max-w-full");
    },
  );

  test("a row is never divided between two pages", () => {
    const { container } = renderWithProviders(<PrintSheet batch={codeBatch()} codes={codes(40)} />);

    expect(rows(container)).toHaveLength(14);
    for (const row of rows(container)) expect(row).toHaveClass("break-inside-avoid");
  });

  test("dashed hairlines in ink show where to cut: between the rows and between the cards of a row", () => {
    const { container } = renderWithProviders(<PrintSheet batch={codeBatch()} codes={codes(6)} />);
    const [first, second] = rows(container);

    // No line above the first row; one in the middle of the gap above every other.
    expect(first).not.toHaveClass("border-t");
    expect(second).toHaveClass("border-t", "border-dashed", "border-ink", "mt-[3mm]", "pt-[3mm]");
    // No line before the first card of a row; one in the middle of the gap before the others.
    const [left, middle, right] = cardsOf(second!);
    expect(left!.className).not.toContain("before:border-l");
    for (const card of [middle!, right!]) {
      expect(card).toHaveClass(
        "before:border-l",
        "before:border-dashed",
        "before:border-ink",
        "before:-left-[3mm]",
      );
    }
  });

  test("a QR is 40 mm wide with a quiet zone of four modules, in ink on its light tile as every QR of the app", () => {
    const { container } = renderWithProviders(<PrintSheet batch={codeBatch()} codes={codes(1)} />);

    const tile = sheet(container).querySelector<HTMLElement>("[data-slot=print-qr]")!;
    expect(tile).toHaveClass("w-[40mm]", "rounded-lg", "bg-foreground", "text-ink");
    // The tile is the quiet zone: the code fills it and brings its four modules of margin.
    expect(tile.querySelector("svg")).toHaveAttribute("data-margin", "4");
    expect(tile.querySelector("svg")).toHaveClass("w-full");
    expect(tile.className).not.toMatch(/\bp[xytblr]?-/);
  });

  test("a card is a surface of the app on paper: navy, with the radius of a card and the text colours of the app", () => {
    const { container } = renderWithProviders(<PrintSheet batch={codeBatch()} codes={codes(4)} />);

    for (const card of container.querySelectorAll("[data-slot=print-card]")) {
      expect(card).toHaveClass("bg-card", "rounded-lg", "text-foreground");
      expect(card.className).not.toMatch(/\bshadow|\bborder(?!-)/);
    }
    // The page around the cards stays paper: the sheet itself has no fill.
    expect(sheet(container).className).not.toMatch(/\bbg-/);
    expect(sheet(container).querySelector("header")!.className).not.toMatch(/\bbg-/);
  });

  test("the head of the sheet: the dark logo, 10 mm tall, the batch's label and until when its codes are valid", () => {
    const { container } = renderWithProviders(
      <PrintSheet
        batch={codeBatch({ label: "Welcome week", expires_at: "2026-12-31T22:59:00Z" })}
        codes={codes(1)}
      />,
    );

    const head = sheet(container).querySelector("header")!;
    const logo = head.querySelector("img")!;
    expect(logo).toHaveAttribute("alt", "MarkETH");
    expect(logo).toHaveAttribute("data-brand", "logo-lockup-dark");
    expect(logo).toHaveClass("h-[10mm]");
    expect(logo).not.toHaveClass("h-12");
    expect(head).toHaveTextContent("Welcome week");
    expect(head).toHaveTextContent("Valid until 31 Dec 2026, 23:59");
    // The label leads and the date stands back.
    expect(within(head).getByText("Welcome week")).toHaveClass("type-label");
    expect(within(head).getByText(/Valid until/)).toHaveClass("type-caption");
  });

  test("a hairline in ink under the head parts it from the cards", () => {
    const { container } = renderWithProviders(<PrintSheet batch={codeBatch()} codes={codes(3)} />);

    const head = sheet(container).querySelector("header")!;
    expect(head).toHaveClass("border-b", "border-ink", "pb-[4mm]", "mb-[6mm]");
    // A line, not a cut guide: the dashes are for where the scissors go.
    expect(head).not.toHaveClass("border-dashed");
  });

  test("a card that is cut out says what it is: the logo and the figure at its top, the QR, the code in code style and until when it is valid", () => {
    const { container } = renderWithProviders(
      <PrintSheet
        batch={codeBatch({ value_cr: "35", expires_at: "2026-12-31T22:59:00Z" })}
        codes={codes(1)}
      />,
    );

    const card = container.querySelector<HTMLElement>("[data-slot=print-card]")!;
    const [top, qr, text] = [...card.children] as HTMLElement[];
    // The top of a card is the top bar of the app: the logo at the left, the pill at the right.
    const logo = top!.querySelector("img[data-brand=logo-lockup]")!;
    expect(logo).toHaveAttribute("alt", "MarkETH");
    expect(logo).toHaveClass("h-[5mm]");
    const pill = top!.querySelector("[data-slot=print-value]")!;
    expect(pill).toHaveClass("rounded-full", "bg-raised");
    expect(pill.querySelector("[data-slot=amount] .sr-only")).toHaveTextContent("35 tokens");
    expect(qr).toHaveAttribute("data-slot", "print-qr");
    expect(text!.querySelector("[data-slot=code]")).toHaveClass("type-code", "type-number");
    expect(text!.querySelector("[data-slot=code]")).toHaveTextContent("CODE-0000-0000");
    const valid = within(text!).getByText(/Valid until/);
    expect(valid).toHaveTextContent("Valid until 31 Dec 2026, 23:59");
    expect(valid).toHaveClass("type-caption", "text-muted-foreground");
  });

  test("what a code is for stands on every card as the bean on its disc and the figure, and the bean is asked for at once", () => {
    const { container } = renderWithProviders(
      <PrintSheet batch={codeBatch({ value_cr: "12.519999" })} codes={codes(2)} />,
    );

    for (const card of container.querySelectorAll("[data-slot=print-card]")) {
      const amount = card.querySelector("[data-slot=amount]")!;
      // Cut after the second decimal, never rounded up.
      expect(amount.querySelector(".sr-only")).toHaveTextContent("12.51 tokens");
      const bean = amount.querySelector("[data-slot=token-icon] img")!;
      expect(bean).toHaveAttribute("data-brand", "token");
      // A sheet is never scrolled into view: an image that waited for that would be missing on paper.
      expect(bean).toHaveAttribute("loading", "eager");
    }
  });

  test("with codes the sheet is all that reaches the paper: its rules are A4 with 12 mm margins and nothing else of the page", () => {
    const { container } = renderWithProviders(<PrintSheet batch={codeBatch()} codes={codes(3)} />);

    const paper = sheet(container);
    expect(paper).toHaveClass("hidden", "print:block");
    const rules = paper.querySelector("style")!.textContent!;
    expect(rules).toContain("@page { size: A4 portrait; margin: 12mm; }");
    expect(rules).toContain('body > :not([data-slot="print-sheet"]) { display: none !important; }');
    expect(rules).toContain("@media print");
    // The fills of the cards reach the paper whatever the print dialog says about backgrounds.
    expect(rules).toMatch(
      /\[data-slot="print-sheet"\], \[data-slot="print-sheet"\] \* \{\s*-webkit-print-color-adjust: exact;\s*print-color-adjust: exact;\s*\}/,
    );
  });

  test.each([
    ["while it has not been asked for", null],
    ["for a batch with no code left", []],
  ])("%s it has no card and no rules, and a print of the page is the page", (_name, none) => {
    const rendered = renderWithProviders(<PrintSheet batch={codeBatch()} codes={none} />);

    const paper = sheet(rendered.container);
    expect(rows(rendered.container)).toHaveLength(0);
    expect(paper.querySelector("style")).toBeNull();
    expect(paper).toHaveClass("hidden");
    expect(paper).not.toHaveClass("print:block");
    // Its head is there all the same, so that the logo is loaded by the time the cards are.
    expect(paper.querySelector("header img")).not.toBeNull();
  });
});
