import { screen, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, test } from "vitest";

import { AppShell } from "@/components/shell";
import { config, me } from "@/test/fixtures";
import { mockApi } from "@/test/http";
import { navigation } from "@/test/navigation";
import { renderWithProviders } from "@/test/render";

import { PolicyCheckbox } from "./policy-checkbox";
import { PolicyForVisitor, PolicyScreen } from "./policy-screen";

const HEADINGS = [
  "What MarkETH is",
  "Markets you may propose",
  "Markets you may not propose",
  "Fair play",
  "Live chat",
  "Vouchers",
  "Live streams",
  "What other players can see about you",
  "What VIS may do",
];

const sections = () =>
  screen.getAllByRole("heading", { level: 2 }).map((heading) => heading.textContent);
const text = () => document.querySelector("[data-slot=policy-text]") as HTMLElement;
/** One section of the text, which its heading names. */
const section = (title: string) => screen.getByRole("region", { name: title });
/** What stands under a heading, line by line: its paragraphs and the points of its lists. */
const lines = (title: string) =>
  Array.from(section(title).querySelectorAll("p, li")).map((line) => line.textContent);

describe("Usage policy", () => {
  test("is an inner screen titled Usage policy, with the text under its nine headings", () => {
    navigation.go("/policy");
    renderWithProviders(
      <AppShell>
        <PolicyScreen />
      </AppShell>,
    );

    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Usage policy");
    expect(screen.getByRole("button", { name: "Back" })).toBeInTheDocument();
    expect(sections()).toEqual(HEADINGS);
    // A heading in the look of h3, a paragraph in body, 24 px between two sections.
    for (const heading of screen.getAllByRole("heading", { level: 2 })) {
      expect(heading).toHaveClass("type-h3");
      expect(heading.parentElement).toHaveClass("gap-2");
    }
    expect(text()).toHaveClass("type-body", "text-foreground", "gap-6");
    // A column of at most 720 px.
    expect(screen.getByRole("main")).toHaveClass("md:max-w-[45rem]");
    expect(screen.getByRole("main")).not.toHaveClass("lg:max-w-[67.5rem]");
  });

  test("no tab is active on it", () => {
    navigation.go("/policy");
    renderWithProviders(
      <AppShell>
        <PolicyScreen />
      </AppShell>,
    );

    for (const link of within(screen.getByRole("navigation", { name: "Main" })).getAllByRole(
      "link",
    )) {
      expect(link).not.toHaveAttribute("aria-current");
    }
  });

  test("asks the API for nothing: its figures are those of the /config that start-up read", () => {
    const api = mockApi();
    renderWithProviders(<PolicyScreen />, {
      config: config({
        signup_grant_cr: "750",
        monthly_grant_cr: "150",
        max_market_lifetime_days: 60,
        max_resolution_delay_days: 10,
        max_proposals_per_day: 5,
        max_stake_per_market_cr: "80",
        voucher_expiry_days: 45,
      }),
    });

    expect(text()).toHaveTextContent(
      "You get 750 welcome tokens when you create your profile and 150 monthly tokens every month, counted from that day.",
    );
    expect(text()).toHaveTextContent(
      "Trading runs for at most 60 days, and the result has to be known at most 10 days after trading closes.",
    );
    expect(text()).toHaveTextContent("You can propose 5 markets a day.");
    expect(text()).toHaveTextContent(
      "You can have at most 80 tokens at stake in one market, so that no single trader sets a price.",
    );
    expect(text()).toHaveTextContent("A voucher is valid for 45 days.");
    expect(api.requests).toHaveLength(0);
  });

  test("a noun after a figure follows its number", () => {
    renderWithProviders(<PolicyScreen />, {
      config: config({
        signup_grant_cr: "1",
        monthly_grant_cr: "1",
        max_market_lifetime_days: 1,
        max_resolution_delay_days: 1,
        max_proposals_per_day: 1,
        max_stake_per_market_cr: "1",
        voucher_expiry_days: 1,
      }),
    });

    expect(text()).toHaveTextContent(
      "You get 1 welcome token when you create your profile and 1 monthly token every month",
    );
    expect(text()).toHaveTextContent(
      "Trading runs for at most 1 day, and the result has to be known at most 1 day after trading closes.",
    );
    expect(text()).toHaveTextContent("You can propose 1 market a day.");
    expect(text()).toHaveTextContent("You can have at most 1 token at stake in one market");
    expect(text()).toHaveTextContent("A voucher is valid for 1 day.");
  });

  test("a large figure is grouped like every amount, and stays the string that was sent", () => {
    renderWithProviders(<PolicyScreen />, {
      config: config({ signup_grant_cr: "1250.5", max_stake_per_market_cr: "1000" }),
    });

    expect(text()).toHaveTextContent("You get 1,250.5 welcome tokens");
    expect(text()).toHaveTextContent("at most 1,000 tokens at stake");
  });

  test("says what is and is not sold, who settles a market and what others can see", () => {
    renderWithProviders(<PolicyScreen />);

    expect(text()).toHaveTextContent(
      "Nothing is sold. Tokens, codes and vouchers cannot be bought, from VIS or from anyone else, and none of them can be turned into money. No real money is involved at any point.",
    );
    expect(text()).toHaveTextContent("An admin who traded a market does not settle it.");
    expect(text()).toHaveTextContent(
      "They do not see your name or your email address. Both come from your ETH sign-in and are shown to you and to the admins of VIS only.",
    );
    expect(text()).toHaveTextContent("change this policy. This page shows the policy in force.");
    // The lists are plain bulleted lists: 4, 5, 6, 4 and 6 points.
    expect(
      screen.getAllByRole("list").map((list) => within(list).getAllByRole("listitem").length),
    ).toEqual([4, 5, 6, 4, 6]);
  });

  test("Live chat says who reads a message, how VIS writes, the four rules, and that a removed message is kept on record", () => {
    renderWithProviders(<PolicyScreen />);

    expect(lines("Live chat")).toEqual([
      "While a market has a live stream, it has a live chat. What you write there is shown at once, with your username, to everyone who can see the market. The chat ends with the stream, and its messages are deleted then.",
      "Each writer in a live chat has a role badge: User, Admin or Barista.",
      "In a live chat:",
      "write about the market;",
      "do not insult, threaten or harass anyone;",
      "do not post the private matters of a person, advertising, or the same thing again and again;",
      "do not try to mislead other traders about an outcome.",
      "What you write is shown as plain text. Links in it cannot be tapped.",
      "VIS removes messages that break this policy. What is removed disappears for everyone. VIS keeps removed messages on record.",
    ]);
    // The four rules are the one list of the section.
    expect(within(section("Live chat")).getAllByRole("listitem")).toHaveLength(4);
  });

  test("Live streams says who may ask for one, what it may show, and that no picture and no sound is kept", () => {
    renderWithProviders(<PolicyScreen />);

    expect(lines("Live streams")).toEqual([
      "Whoever proposed a market can ask VIS to stream it live, from a camera. An admin decides on every request, and VIS can end a live stream at any moment.",
      "A live stream shows what its market is about. Film nobody who has not agreed to it, and show nothing this policy keeps out of markets.",
      "Viewers see a live stream a few seconds late. No picture and no sound is kept; that a stream took place is.",
    ]);
  });

  test("what others can see names a chat message, the camera of whoever streams, and the proposer who streams", () => {
    renderWithProviders(<PolicyScreen />);

    const [shown, hidden] = lines("What other players can see about you");
    expect(shown).toMatch(
      / They also see what you write in a live chat\. If you stream a market live, viewers see and hear what your camera shows\.$/,
    );
    expect(hidden).toMatch(
      / They are not shown which markets you trade or what you hold in them, and a market does not show who proposed it, unless you stream it live yourself\.$/,
    );
  });

  test("VIS may remove a chat message: the third point of what VIS may do", () => {
    renderWithProviders(<PolicyScreen />);

    const points = within(section("What VIS may do"))
      .getAllByRole("listitem")
      .map((point) => point.textContent);
    expect(points[2]).toBe("remove a chat message that breaks this policy;");
  });

  test("is read, and left with the back arrow: no illustration, no campus line, no button of its own", () => {
    renderWithProviders(<PolicyScreen />);

    expect(within(screen.getByRole("main")).queryByRole("button")).not.toBeInTheDocument();
    expect(within(screen.getByRole("main")).queryByRole("link")).not.toBeInTheDocument();
    expect(within(screen.getByRole("main")).queryByRole("img")).not.toBeInTheDocument();
    expect(text().querySelector("svg")).toBeNull();
  });
});

describe("Usage policy for a visitor without a profile", () => {
  test("stands on the frame of the boot screen: the logo and nothing on its right, no tab bar, the title above the text", () => {
    navigation.go("/policy");
    // Nobody has a profile here: nothing of one is in the cache.
    const api = mockApi();
    renderWithProviders(<PolicyForVisitor />, { me: null });

    const bar = document.querySelector("[data-slot=top-bar]") as HTMLElement;
    expect(within(bar).getByRole("img", { name: "MarkETH" })).toBeInTheDocument();
    expect(within(bar).queryByRole("link", { name: /Wallet/ })).not.toBeInTheDocument();
    expect(within(bar).queryByRole("link", { name: "Profile" })).not.toBeInTheDocument();
    expect(within(bar).queryByRole("button", { name: "Back" })).not.toBeInTheDocument();
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();

    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    const title = screen.getByRole("heading", { level: 1, name: "Usage policy" });
    expect(title).toHaveClass("type-h1");
    expect(screen.getByRole("main")).toContainElement(title);
    expect(sections()).toEqual(HEADINGS);
    expect(text()).toHaveTextContent("You get 500 welcome tokens");
    expect(screen.getByRole("main")).toHaveClass("md:max-w-[45rem]");
    expect(api.requests).toHaveLength(0);
  });

  test("the logo leads to the profile set-up", () => {
    renderWithProviders(<PolicyForVisitor />, { me: null });

    const links = screen.getAllByRole("link");
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAccessibleName("MarkETH");
    // Every route but this one shows the set-up to a visitor without a profile.
    expect(links[0]).toHaveAttribute("href", "/");
  });

  test("for a visitor who came from the set-up the logo is one step back, to the address it stood at", async () => {
    navigation.go("/wallet?code=ABCD-EFGH-JKMN");
    navigation.router.push("/policy");
    const { user } = renderWithProviders(<PolicyForVisitor fromSetup />, { me: null });

    await user.click(screen.getByRole("link", { name: "MarkETH" }));

    expect(navigation.href).toBe("/wallet?code=ABCD-EFGH-JKMN");
  });
});

describe("PolicyCheckbox", () => {
  function Form({ error }: { error?: string }) {
    const [accepted, setAccepted] = useState(false);
    return <PolicyCheckbox checked={accepted} onChange={setAccepted} error={error} />;
  }

  const box = () => screen.getByRole("checkbox", { name: "I accept the usage policy" });

  test("is named by its whole text and starts unticked", () => {
    renderWithProviders(<Form />, { me: me() });

    expect(box()).not.toBeChecked();
    expect(box()).not.toBeInvalid();
  });

  test("the words usage policy are a link to /policy that opens in the same tab", () => {
    renderWithProviders(<Form />);

    const link = screen.getByRole("link", { name: "usage policy" });
    expect(link).toHaveAttribute("href", "/policy");
    // Another tab has no way back to the form.
    expect(link).not.toHaveAttribute("target");
  });

  test("a tap on the text ticks the box; a tap on the link leaves it as it is", async () => {
    const { user } = renderWithProviders(<Form />);

    await user.click(screen.getByRole("link", { name: "usage policy" }));
    expect(box()).not.toBeChecked();

    await user.click(screen.getByText("I accept the"));
    expect(box()).toBeChecked();
  });

  test("the API's sentence for policy_accepted stands under it", () => {
    renderWithProviders(<Form error="Accept the usage policy to set up your profile." />);

    expect(box()).toBeInvalid();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Accept the usage policy to set up your profile.",
    );
  });
});
