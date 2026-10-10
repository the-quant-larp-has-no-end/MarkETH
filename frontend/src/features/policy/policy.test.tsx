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
  "Vouchers",
  "What other players can see about you",
  "What VIS may do",
];

const sections = () =>
  screen.getAllByRole("heading", { level: 2 }).map((heading) => heading.textContent);
const text = () => document.querySelector("[data-slot=policy-text]") as HTMLElement;

describe("Usage policy", () => {
  test("is an inner screen titled Usage policy, with the text under its seven headings", () => {
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
    // The lists are plain bulleted lists: 4, 5, 6 and 5 points.
    expect(
      screen.getAllByRole("list").map((list) => within(list).getAllByRole("listitem").length),
    ).toEqual([4, 5, 6, 5]);
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

  test("the words usage policy are a link to /policy that opens in a tab of its own, and says so", () => {
    renderWithProviders(<Form />);

    const link = screen.getByRole("link", { name: "usage policy" });
    expect(link).toHaveAttribute("href", "/policy");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAccessibleDescription("Opens in another tab.");
    // The note is for a screen reader, and no part of the checkbox's name.
    expect(screen.queryByText("Opens in another tab.")).not.toBeVisible();
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
