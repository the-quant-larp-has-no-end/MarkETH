import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { LiveBadge as LiveBadgeOfMarket } from "@/components/market";

import { LiveBadge } from "./live-badge";

const badge = () => screen.getByText("Live");

describe("LiveBadge", () => {
  test("says Live as a badge does: caption, capitals, the small radius, 4 x 8 px", () => {
    render(<LiveBadge />);

    expect(badge()).toHaveAttribute("data-slot", "badge");
    expect(badge()).toHaveClass("type-caption", "uppercase", "rounded-sm", "px-2", "py-1");
  });

  test("is filled with the white of the accent and written in navy", () => {
    render(<LiveBadge />);

    expect(badge()).toHaveClass("bg-foreground", "text-primary-foreground");
    // One fill and one colour of text: those of the badge it is built on give way.
    expect(badge()).not.toHaveClass("bg-raised");
    expect(badge()).not.toHaveClass("text-foreground");
  });

  test("has no colour of its own: nothing in it is green or red", () => {
    const { container } = render(<LiveBadge />);

    expect(container.innerHTML).not.toMatch(/(bg|text|border|fill|stroke)-(yes|no|destructive)\b/);
  });

  test("keeps its fill whatever the place it stands in adds", () => {
    render(<LiveBadge className="self-start bg-no text-ink" />);

    expect(badge()).toHaveClass("self-start", "bg-foreground", "text-primary-foreground");
    expect(badge()).not.toHaveClass("bg-no");
    expect(badge()).not.toHaveClass("text-ink");
  });

  test("the radio icon stands before the word, 12 px, in the colour of the word", () => {
    render(<LiveBadge />);

    const icon = badge().querySelector("svg")!;
    expect(icon).toHaveClass("lucide-radio");
    // The first thing in the badge, and its child: the badge sizes the icons it holds.
    expect(badge().firstChild).toBe(icon);
    expect(badge()).toHaveClass("[&>svg]:size-3");
    expect(icon).toHaveAttribute("stroke", "currentColor");
  });

  test("a screen reader hears Live: the icon is hidden and nothing else names the badge", () => {
    render(<LiveBadge />);

    expect(badge()).toHaveTextContent(/^Live$/);
    expect(badge().querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(badge()).not.toHaveAttribute("aria-label");
    expect(badge()).not.toHaveAttribute("title");
    expect(badge().querySelector("title")).not.toBeInTheDocument();
  });

  test("does not pulse, blink or move", () => {
    const { container } = render(<LiveBadge />);

    for (const element of container.querySelectorAll("*")) {
      expect(element.getAttribute("class") ?? "").not.toMatch(/animate|transition|duration|motion/);
    }
    expect(container.querySelector("animate, animateMotion, animateTransform, set")).toBeNull();
  });

  test("is what components/market exports", () => {
    expect(LiveBadgeOfMarket).toBe(LiveBadge);
  });
});
