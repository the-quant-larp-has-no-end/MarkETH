import { describe, expect, test, vi } from "vitest";

import { codeBatch } from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";

import { PrintSheet } from "./print-sheet";

/* What a QR holds cannot be read off its picture: the test looks at what it was given. */
vi.mock("qrcode.react", () => ({
  QRCodeSVG: ({ value, marginSize }: { value: string; marginSize?: number }) => (
    <svg data-value={value} data-margin={marginSize} />
  ),
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
    // Three equal columns, 6 mm apart, on a sheet as wide as A4 leaves between 12 mm margins.
    for (const row of rows(container)) expect(row).toHaveClass("grid-cols-3", "gap-[6mm]");
    expect(sheet(container)).toHaveClass("w-[186mm]", "max-w-full");
  });

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

  test("a QR is 40 mm wide with a quiet zone of four modules, in ink on the paper itself", () => {
    const { container } = renderWithProviders(<PrintSheet batch={codeBatch()} codes={codes(1)} />);

    const paper = sheet(container);
    expect(paper).toHaveClass("text-ink");
    expect(paper.querySelector("[data-slot=print-card] svg")).toHaveAttribute("data-margin", "4");
    // Nothing on the sheet has a fill: it does not depend on whether backgrounds are printed.
    expect(paper.querySelector("[class*=bg-]")).toBeNull();
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
    expect(logo.getAttribute("src")).toContain("logo-lockup-dark.png");
    expect(logo).toHaveClass("h-[10mm]");
    expect(logo).not.toHaveClass("h-12");
    expect(head).toHaveTextContent("Welcome week");
    expect(head).toHaveTextContent("Valid until 31 Dec 2026, 23:59");
  });

  test("what a code is for stands under it as the bean and the figure, and the bean is asked for at once", () => {
    const { container } = renderWithProviders(
      <PrintSheet batch={codeBatch({ value_cr: "12.519999" })} codes={codes(2)} />,
    );

    for (const card of container.querySelectorAll("[data-slot=print-card]")) {
      const amount = card.querySelector("[data-slot=amount]")!;
      // Cut after the second decimal, never rounded up.
      expect(amount.querySelector(".sr-only")).toHaveTextContent("12.51 tokens");
      const bean = amount.querySelector("img")!;
      expect(decodeURIComponent(bean.getAttribute("src")!)).toContain("/brand/token.png");
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
