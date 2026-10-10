import { render, screen, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "./table";

function Users({ tall }: { tall?: boolean }) {
  return (
    <Table aria-label="Users">
      <TableHeader>
        <TableRow>
          <TableHead scope="col">Player</TableHead>
          <TableHead scope="col">ETH sign-in</TableHead>
          <TableHead scope="col">Status</TableHead>
          <TableHead scope="col" numeric>
            Tokens
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        <TableRow tall={tall}>
          <TableCell>A question that is long enough to need two lines of the table</TableCell>
          <TableCell clip>bea.beispiel@ethz.ch</TableCell>
          <TableCell nowrap>Suspended</TableCell>
          <TableCell numeric>1,015</TableCell>
        </TableRow>
      </TableBody>
    </Table>
  );
}

const cell = (name: string | RegExp) => screen.getByRole("cell", { name });

describe("Table", () => {
  test("a heading row 44 px tall in caption, muted, and rows at least 44 px tall with a divider", () => {
    render(<Users />);

    const [head, row] = screen.getAllByRole("row");
    for (const heading of within(head!).getAllByRole("columnheader")) {
      expect(heading).toHaveClass("h-11", "type-caption", "text-muted-foreground");
    }
    expect(row).toHaveClass("h-11", "border-b");
    // No outline around the table: lines between the rows only.
    expect(screen.getByRole("table", { name: "Users" })).not.toHaveClass("border");
  });

  test("a row of the Leaderboard is 56 px tall", () => {
    render(<Users tall />);

    expect(screen.getAllByRole("row")[1]).toHaveClass("h-14");
    expect(screen.getAllByRole("row")[1]).not.toHaveClass("h-11");
  });

  test("a column of text is left aligned and gives way by wrapping", () => {
    render(<Users />);

    expect(cell(/A question that is long enough/)).toHaveClass("wrap-break-word");
    expect(cell(/A question that is long enough/)).not.toHaveClass("whitespace-nowrap", "w-px");
    expect(screen.getByRole("columnheader", { name: "Player" })).toHaveClass("text-left");
  });

  test("text that has to stay one line gives way by being cut with an ellipsis", () => {
    render(<Users />);

    expect(cell("bea.beispiel@ethz.ch")).toHaveClass("max-w-0", "truncate");
  });

  test("a column of figures is right aligned, in number, as wide as its widest figure, and never shrinks", () => {
    render(<Users />);

    expect(cell("1,015")).toHaveClass("text-right", "type-number", "whitespace-nowrap", "w-px");
    expect(screen.getByRole("columnheader", { name: "Tokens" })).toHaveClass("text-right");
  });

  test("a badge, a date or a button keeps its line and its width", () => {
    render(<Users />);

    expect(cell("Suspended")).toHaveClass("whitespace-nowrap", "w-px");
    expect(cell("Suspended")).not.toHaveClass("text-right");
  });

  test("a table that is wider than its column scrolls sideways inside its own box", () => {
    render(<Users />);

    const box = screen.getByRole("table").parentElement;
    expect(box).toHaveAttribute("data-slot", "table-container");
    expect(box).toHaveClass("w-full", "overflow-x-auto");
  });
});
