import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "./tabs";

function Views(props: Partial<React.ComponentProps<typeof TabsTrigger>>) {
  return (
    <Tabs defaultValue="positions">
      <TabsList aria-label="Portfolio">
        <TabsTrigger value="positions">Positions</TabsTrigger>
        <TabsTrigger value="history" {...props}>
          History
        </TabsTrigger>
      </TabsList>
      <TabsContent value="positions">Open positions</TabsContent>
      <TabsContent value="history">Past trades</TabsContent>
    </Tabs>
  );
}

describe("Tabs", () => {
  test("a row 48 px tall with label text, the active tab white with a line under it", () => {
    render(<Views />);

    expect(screen.getByRole("tablist", { name: "Portfolio" })).toHaveClass("h-12", "border-b");
    const active = screen.getByRole("tab", { name: "Positions" });
    expect(active).toHaveAttribute("aria-selected", "true");
    expect(active).toHaveClass(
      "h-12",
      "type-label",
      "data-[state=active]:text-foreground",
      "data-[state=active]:after:opacity-100",
    );
    expect(screen.getByRole("tab", { name: "History" })).toHaveClass("text-muted-foreground");
  });

  test("the focus ring of a tab is drawn inside it, where the scrolling row does not cut it off", () => {
    render(<Views />);

    expect(screen.getByRole("tab", { name: "History" })).toHaveClass(
      "focus-visible:-outline-offset-2",
    );
  });

  test("a tab chosen with the pointer shows its view and no focus ring", async () => {
    const user = userEvent.setup();
    render(<Views />);
    const history = screen.getByRole("tab", { name: "History" });
    expect(history).toHaveClass("data-by-pointer:outline-none");
    expect(history).not.toHaveAttribute("data-by-pointer");

    await user.click(history);

    expect(history).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("Past trades");
    expect(history).toHaveFocus();
    expect(history).toHaveAttribute("data-by-pointer");
  });

  test("the ring is back as soon as a key is pressed on that tab", async () => {
    const user = userEvent.setup();
    render(<Views />);
    const history = screen.getByRole("tab", { name: "History" });
    await user.click(history);
    expect(history).toHaveAttribute("data-by-pointer");

    fireEvent.keyDown(history, { key: "Shift" });

    expect(history).toHaveFocus();
    expect(history).not.toHaveAttribute("data-by-pointer");
  });

  test("a tab the focus has left forgets the pointer, so the keyboard finds it with its ring", async () => {
    const user = userEvent.setup();
    render(<Views />);
    const positions = screen.getByRole("tab", { name: "Positions" });
    const history = screen.getByRole("tab", { name: "History" });
    await user.click(history);

    await user.keyboard("{ArrowLeft}");
    expect(positions).toHaveFocus();
    expect(positions).not.toHaveAttribute("data-by-pointer");

    await user.keyboard("{ArrowRight}");
    expect(history).toHaveFocus();
    expect(history).not.toHaveAttribute("data-by-pointer");
  });

  test("a tab reached with the keyboard keeps its ring", async () => {
    const user = userEvent.setup();
    render(<Views />);

    await user.tab();
    await user.keyboard("{ArrowRight}");

    const history = screen.getByRole("tab", { name: "History" });
    expect(history).toHaveFocus();
    expect(history).toHaveAttribute("aria-selected", "true");
    expect(history).not.toHaveAttribute("data-by-pointer");
  });

  test("a press that focuses nothing leaves no trace: the keyboard still finds the tab with its ring", async () => {
    const user = userEvent.setup();
    render(<Views />);
    const history = screen.getByRole("tab", { name: "History" });

    // The other mouse button: the primitive neither focuses nor chooses the tab.
    fireEvent.mouseDown(history, { button: 2 });
    expect(history).not.toHaveFocus();
    expect(history).toHaveAttribute("aria-selected", "false");

    await user.tab();
    await user.keyboard("{ArrowRight}");

    expect(history).toHaveFocus();
    expect(history).not.toHaveAttribute("data-by-pointer");
  });

  test("a row wider than the screen keeps the active tab in sight, when it appears and when another becomes active", async () => {
    // The row ends at 200 px; the History tab lies beyond it, the Positions tab inside.
    const box = (left: number, right: number) =>
      ({ left, right, top: 0, bottom: 48, width: right - left, height: 48 }) as DOMRect;
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (
      this: Element,
    ) {
      if (this.getAttribute("role") === "tablist") return box(16, 200);
      if (this.textContent === "History") return box(260, 340);
      if (this.textContent === "Positions") return box(40, 140);
      return box(0, 0);
    });
    const intoView = vi.spyOn(Element.prototype, "scrollIntoView");
    const user = userEvent.setup();
    render(<Views />);
    const row = screen.getByRole("tablist", { name: "Portfolio" });
    expect(row).toHaveClass("overflow-x-auto");
    // The active tab is in sight: nothing moves.
    expect(row.scrollLeft).toBe(0);

    screen.getByRole("tab", { name: "Positions" }).focus();
    await user.keyboard("{ArrowRight}");

    await vi.waitFor(() => expect(row.scrollLeft).toBe(140));
    // Only the row moved, never the page.
    expect(intoView).not.toHaveBeenCalled();
  });

  test("handlers of the screen still run", async () => {
    const user = userEvent.setup();
    const onMouseDown = vi.fn();
    const onFocus = vi.fn();
    const onKeyDown = vi.fn();
    const onBlur = vi.fn();
    render(
      <Views onMouseDown={onMouseDown} onFocus={onFocus} onKeyDown={onKeyDown} onBlur={onBlur} />,
    );

    await user.click(screen.getByRole("tab", { name: "History" }));
    await user.keyboard("{ArrowLeft}");

    expect(onMouseDown).toHaveBeenCalledTimes(1);
    expect(onFocus).toHaveBeenCalledTimes(1);
    expect(onKeyDown).toHaveBeenCalledTimes(1);
    expect(onBlur).toHaveBeenCalledTimes(1);
  });

  test("the view of a tab takes the keyboard's focus and shows it", async () => {
    const user = userEvent.setup();
    render(<Views />);

    await user.tab();
    await user.tab();

    const view = screen.getByRole("tabpanel");
    expect(view).toHaveFocus();
    expect(view).not.toHaveClass("outline-none");
  });
});
