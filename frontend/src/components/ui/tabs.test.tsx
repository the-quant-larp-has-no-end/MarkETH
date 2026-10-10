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

  test("handlers of the screen still run", async () => {
    const user = userEvent.setup();
    const onPointerDown = vi.fn();
    const onKeyDown = vi.fn();
    const onBlur = vi.fn();
    render(<Views onPointerDown={onPointerDown} onKeyDown={onKeyDown} onBlur={onBlur} />);

    await user.click(screen.getByRole("tab", { name: "History" }));
    await user.keyboard("{ArrowLeft}");

    expect(onPointerDown).toHaveBeenCalledTimes(1);
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
