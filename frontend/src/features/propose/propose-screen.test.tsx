import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { marketDetail, proposal } from "@/test/fixtures";
import { deferred, json, mockApi, noAnswer, type Route } from "@/test/http";
import { renderWithProviders } from "@/test/render";

import { ProposeScreen } from "./propose-screen";

const PROPOSE: Route = "POST /api/markets";
const PROPOSALS: Route = "GET /api/me/proposals";

const QUESTION = "Will the Mensa Polyterrasse open on Saturdays before the summer?";

type User = ReturnType<typeof renderWithProviders>["user"];

const field = (label: string) => screen.getByLabelText(label);
const submit = () => screen.getByRole("button", { name: "Submit for review" });
const listed = () =>
  within(document.querySelector<HTMLElement>("[data-slot=my-proposals] [data-slot=row-list]")!)
    .getAllByRole("listitem")
    .map((row) => row.textContent);

async function fillIn(user: User) {
  for (const [label, text] of [
    ["Question", QUESTION],
    ["YES means", "The Mensa announces Saturday opening hours."],
    ["NO means", "No Saturday opening hours are announced."],
    ["How it is decided", "Decided from the opening hours on the Mensa's website."],
    ["Source", "ethz.ch gastronomy"],
  ] as const) {
    await user.click(field(label));
    await user.paste(text);
  }
  await user.type(field("Trading closes"), "2026-12-11T18:00");
  await user.type(field("Decided by"), "2026-12-18T18:00");
}

const earlier = proposal({ id: "earlier", question: "Will the Polybahn be free in 2027?" });
const arrived = proposal({ id: "arrived", question: QUESTION });

describe("Propose market", () => {
  test("is an inner screen titled Propose a market, with the form and My proposals", async () => {
    mockApi({ [PROPOSALS]: json(200, { items: [earlier] }) });
    renderWithProviders(<ProposeScreen />);

    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Propose a market");
    expect(screen.getByRole("button", { name: "Back" })).toBeInTheDocument();
    expect(field("Question")).toBeInTheDocument();
    expect(await screen.findByText(earlier.question)).toBeInTheDocument();
    // One primary action on the screen.
    expect(document.querySelectorAll("[data-slot=button][data-variant=primary]")).toHaveLength(1);
  });

  test("a proposal that was sent shows the toast, empties the form and stands at the top of the list", async () => {
    const api = mockApi({
      [PROPOSALS]: json(200, { items: [earlier] }),
      [PROPOSE]: json(201, marketDetail({ id: "arrived", question: QUESTION, status: "PROPOSED" })),
    });
    const { user } = renderWithProviders(<ProposeScreen />);
    await screen.findByText(earlier.question);

    await fillIn(user);
    await user.click(screen.getByRole("radio", { name: "Medium" }));
    api.on(PROPOSALS, json(200, { items: [arrived, earlier] }));
    await user.click(submit());

    expect(await screen.findByText("Proposal sent")).toBeInTheDocument();
    await waitFor(() =>
      expect(listed()).toEqual([`${QUESTION} In review`, `${earlier.question} In review`]),
    );
    expect(field("Question")).toHaveValue("");
    expect(field("YES means")).toHaveValue("");
    expect(field("Trading closes")).toHaveValue("");
    expect(screen.getByRole("radio", { name: "Default" })).toBeChecked();
    expect(submit()).toBeDisabled();
    expect(api.calls(PROPOSE)).toHaveLength(1);
  });

  test("without an answer the button waits until the list was read again, and a proposal that arrived is in it", async () => {
    const reread = deferred();
    const api = mockApi({
      [PROPOSALS]: json(200, { items: [earlier] }),
      [PROPOSE]: noAnswer,
    });
    const { user } = renderWithProviders(<ProposeScreen />);
    await screen.findByText(earlier.question);

    await fillIn(user);
    api.on(PROPOSALS, reread.handler);
    await user.click(submit());

    // The request failed and the list is on its way: nothing can be sent meanwhile.
    await waitFor(() => expect(api.calls(PROPOSALS)).toHaveLength(2));
    expect(submit()).toBeDisabled();
    expect(submit()).toHaveAttribute("aria-busy", "true");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await user.click(submit());
    expect(api.calls(PROPOSE)).toHaveLength(1);

    reread.resolve(json(200, { items: [arrived, earlier] }));

    expect(await screen.findByRole("alert")).toHaveTextContent("No connection. Try again.");
    expect(
      screen.getByText("If your proposal arrived, it is in the list below."),
    ).toBeInTheDocument();
    expect(listed()).toEqual([`${QUESTION} In review`, `${earlier.question} In review`]);
    // The form keeps what was typed, and nothing was sent a second time.
    expect(field("Question")).toHaveValue(QUESTION);
    expect(submit()).toBeEnabled();
    expect(api.calls(PROPOSE)).toHaveLength(1);
  });
});
