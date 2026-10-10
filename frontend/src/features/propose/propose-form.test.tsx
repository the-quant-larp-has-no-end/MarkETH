import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import { config, marketDetail } from "@/test/fixtures";
import { apiError, deferred, json, mockApi, noAnswer, type Route } from "@/test/http";
import { renderWithProviders } from "@/test/render";

import { ProposeForm } from "./propose-form";

const PROPOSE: Route = "POST /api/markets";

const QUESTION = "Will the ASVZ open a second climbing wall before the end of the semester?";
const YES = "The ASVZ announces the opening on its website.";
const NO = "No opening is announced by the end of the semester.";
const CRITERIA = "Decided from the ASVZ news page on the last day of the semester.";
const SOURCE = "asvz.ch news";

type User = ReturnType<typeof renderWithProviders>["user"];

const field = (label: string) => screen.getByLabelText(label);
const submit = () => screen.getByRole("button", { name: "Submit for review" });
const tier = (name: string) => screen.getByRole("radio", { name });

async function write(user: User, label: string, text: string) {
  await user.click(field(label));
  await user.paste(text);
}

async function fillIn(user: User) {
  await write(user, "Question", QUESTION);
  await write(user, "YES means", YES);
  await write(user, "NO means", NO);
  await write(user, "How it is decided", CRITERIA);
  await write(user, "Source", SOURCE);
  await user.type(field("Trading closes"), "2026-12-11T18:00");
  await user.type(field("Decided by"), "2026-12-18T18:00");
}

const proposed = () => marketDetail({ status: "PROPOSED", trading_open: false, tier: null });

describe("the form", () => {
  test("has one field per row, in the order of the design", () => {
    renderWithProviders(<ProposeForm />);

    const form = document.querySelector("[data-slot=propose-form]")!;
    const labels = Array.from(form.querySelectorAll("[data-slot=field]")).map(
      (row) => row.querySelector("label, span")?.textContent,
    );

    expect(labels).toEqual([
      "Question",
      "YES means",
      "NO means",
      "How it is decided",
      "Source",
      "Trading closes",
      "Decided by",
      "Liquidity",
    ]);
  });

  test("each text field shows its character limit from /config", () => {
    renderWithProviders(<ProposeForm />, {
      config: config({
        question_min_length: 12,
        question_max_length: 150,
        short_text_max_length: 300,
        long_text_max_length: 900,
      }),
    });

    expect(field("Question")).toHaveAccessibleDescription("12 to 150 characters.");
    expect(field("Question")).toHaveAttribute("maxlength", "150");
    expect(field("YES means")).toHaveAttribute("maxlength", "300");
    expect(field("NO means")).toHaveAttribute("maxlength", "300");
    expect(field("Source")).toHaveAttribute("maxlength", "300");
    expect(field("How it is decided")).toHaveAttribute("maxlength", "900");
    expect(screen.getByText("0 / 150")).toBeInTheDocument();
    expect(screen.getAllByText("0 / 300")).toHaveLength(3);
    expect(screen.getByText("0 / 900")).toBeInTheDocument();
  });

  test("a field counts what is typed against its limit", async () => {
    const { user } = renderWithProviders(<ProposeForm />);

    await write(user, "Source", SOURCE);

    expect(screen.getByText(`${SOURCE.length} / 500`)).toBeInTheDocument();
  });

  test("the limits of the two times and their timezone are stated from /config", () => {
    renderWithProviders(<ProposeForm />, {
      config: config({
        max_market_lifetime_days: 60,
        max_resolution_delay_days: 1,
        timezone: "Europe/Zurich",
      }),
    });

    expect(field("Trading closes")).toHaveAccessibleDescription(
      "At most 60 days from now. Europe/Zurich time.",
    );
    expect(field("Decided by")).toHaveAccessibleDescription(
      "At most 1 day after trading closes. Europe/Zurich time.",
    );
  });

  test("the tiers are the radio cards of /config, each with its name and its line, and Default is chosen", () => {
    renderWithProviders(<ProposeForm />);

    const group = screen.getByRole("radiogroup", { name: "Liquidity" });
    expect(
      within(group)
        .getAllByRole("radio")
        .map((radio) => radio.getAttribute("value")),
    ).toEqual(["TIGHT", "DEFAULT", "MEDIUM"]);
    expect(tier("Tight")).toHaveAccessibleDescription(
      "The price moves fast. For a question few people will trade.",
    );
    expect(tier("Default")).toHaveAccessibleDescription("Right for most questions.");
    expect(tier("Medium")).toHaveAccessibleDescription(
      "The price moves slowly. For a question many people will trade.",
    );
    expect(tier("Default")).toBeChecked();
    expect(tier("Tight")).not.toBeChecked();
  });

  test("a proposal cannot ask for a custom tier, and shows no figure of a tier", () => {
    renderWithProviders(<ProposeForm />, {
      config: config({
        tiers: [
          { tier: "MEDIUM", b_cr: "2000", max_loss_cr: "1386.294362" },
          { tier: "CUSTOM", b_cr: "1", max_loss_cr: "1" },
          { tier: "TIGHT", b_cr: "500", max_loss_cr: "346.573591" },
        ],
      }),
    });

    expect(screen.getAllByRole("radio").map((radio) => radio.getAttribute("value"))).toEqual([
      "MEDIUM",
      "TIGHT",
    ]);
    // Without Default among them none is chosen, and the proposer has to choose.
    expect(screen.queryByRole("radio", { checked: true })).not.toBeInTheDocument();
    expect(document.querySelector("[data-slot=propose-form] [data-slot=amount]")).toBeNull();
    expect(screen.queryByText(/1386\.29|346\.57/)).not.toBeInTheDocument();
  });

  test("the content rules and the two notes stand above the button", () => {
    renderWithProviders(<ProposeForm />, { config: config({ max_proposals_per_day: 5 }) });

    const rules = screen.getByRole("list", { name: "Content rules" });
    expect(
      within(rules)
        .getAllByRole("listitem")
        .map((item) => item.textContent),
    ).toEqual([
      "No private matters of named people",
      "No grades",
      "No outcomes one trader can decide",
      "No party politics or religion",
    ]);
    expect(screen.getByText("You cannot trade a market you propose.")).toBeInTheDocument();
    expect(screen.getByText("You can propose 5 markets a day.")).toBeInTheDocument();
  });

  test("one proposal a day is one market", () => {
    renderWithProviders(<ProposeForm />, { config: config({ max_proposals_per_day: 1 }) });

    expect(screen.getByText("You can propose 1 market a day.")).toBeInTheDocument();
  });

  test("the button is disabled until every field is filled", async () => {
    const { user } = renderWithProviders(<ProposeForm />);

    expect(submit()).toBeDisabled();
    await write(user, "Question", QUESTION);
    await write(user, "YES means", YES);
    await write(user, "NO means", NO);
    await write(user, "How it is decided", CRITERIA);
    await write(user, "Source", SOURCE);
    await user.type(field("Trading closes"), "2026-12-11T18:00");
    expect(submit()).toBeDisabled();
    await user.type(field("Decided by"), "2026-12-18T18:00");
    expect(submit()).toBeEnabled();
  });

  test("a field of blanks is not filled", async () => {
    const { user } = renderWithProviders(<ProposeForm />);
    await fillIn(user);

    await user.clear(field("Source"));
    await write(user, "Source", "   ");

    expect(submit()).toBeDisabled();
  });
});

describe("submitting", () => {
  test("sends the wording without the blanks around it, the times with the zone's offset and the tier's key", async () => {
    const api = mockApi({ [PROPOSE]: json(201, proposed()) });
    const onSent = vi.fn();
    const { user } = renderWithProviders(<ProposeForm onSent={onSent} />);

    await fillIn(user);
    await write(user, "Question", "  ");
    await user.click(tier("Tight"));
    await user.click(submit());

    await waitFor(() => expect(onSent).toHaveBeenCalledTimes(1));
    expect(api.lastCall(PROPOSE)?.body).toEqual({
      question: QUESTION,
      yes_definition: YES,
      no_definition: NO,
      resolution_criteria: CRITERIA,
      resolution_source: SOURCE,
      close_at: "2026-12-11T18:00:00+01:00",
      resolve_by: "2026-12-18T18:00:00+01:00",
      requested_tier: "TIGHT",
    });
    expect(await screen.findByText("Proposal sent")).toBeInTheDocument();
  });

  test("the button is disabled and shows a spinner until the answer arrives: two taps are one request", async () => {
    const answer = deferred();
    const api = mockApi({ [PROPOSE]: answer.handler });
    const { user } = renderWithProviders(<ProposeForm />);

    await fillIn(user);
    await user.click(submit());
    await user.click(submit());
    await user.keyboard("{Enter}");

    expect(submit()).toBeDisabled();
    expect(submit()).toHaveAttribute("aria-busy", "true");
    expect(api.calls(PROPOSE)).toHaveLength(1);

    answer.resolve(json(201, proposed()));
    await waitFor(() => expect(submit()).not.toHaveAttribute("aria-busy"));
  });

  test("a rule the service checks appears on the field it names, with the API's message, and the input stays", async () => {
    mockApi({
      [PROPOSE]: apiError(422, "VALIDATION_ERROR", "Trading has to close in the future.", {
        field: "close_at",
      }),
    });
    const { user } = renderWithProviders(<ProposeForm />);

    await fillIn(user);
    await user.click(submit());

    await waitFor(() => expect(field("Trading closes")).toBeInvalid());
    expect(field("Trading closes")).toHaveAccessibleDescription(
      /Trading has to close in the future\./,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Trading has to close in the future.");
    expect(field("Decided by")).not.toBeInvalid();
    expect(field("Question")).toHaveValue(QUESTION);
    expect(field("Trading closes")).toHaveValue("2026-12-11T18:00");
    // The button is at the end of the form: the refused field is brought to the user.
    expect(field("Trading closes")).toHaveFocus();
  });

  test("a request that does not match its schema appears on the fields its locs end in", async () => {
    mockApi({
      [PROPOSE]: apiError(422, "VALIDATION_ERROR", "The request is not valid.", {
        errors: [
          {
            loc: ["body", "question"],
            msg: "String should have at least 10 characters",
            type: "string_too_short",
          },
          {
            loc: ["body", "requested_tier"],
            msg: "Value error, a proposal asks for TIGHT, DEFAULT or MEDIUM; CUSTOM is set by VIS",
            type: "value_error",
          },
        ],
      }),
    });
    const { user } = renderWithProviders(<ProposeForm />);

    await fillIn(user);
    await user.click(submit());

    await waitFor(() => expect(field("Question")).toBeInvalid());
    expect(field("Question")).toHaveAccessibleDescription(
      /String should have at least 10 characters/,
    );
    // Of several refused fields the first one takes the focus.
    expect(field("Question")).toHaveFocus();
    const group = screen.getByRole("radiogroup", { name: "Liquidity" });
    expect(group).toBeInvalid();
    expect(group).toHaveAccessibleDescription(/a proposal asks for TIGHT, DEFAULT or MEDIUM/);
    // Both errors sit on their fields; nothing is repeated above the button.
    expect(screen.queryByText("The request is not valid.")).not.toBeInTheDocument();
  });

  test("LIMIT_EXCEEDED shows the API's message above the button", async () => {
    mockApi({
      [PROPOSE]: apiError(
        409,
        "LIMIT_EXCEEDED",
        "You have reached today's limit of proposals. Try again tomorrow.",
        { limit: "MAX_PROPOSALS_PER_USER_PER_DAY" },
      ),
    });
    const { user } = renderWithProviders(<ProposeForm />);

    await fillIn(user);
    await user.click(submit());

    const message = await screen.findByRole("alert");
    expect(message).toHaveTextContent(
      "You have reached today's limit of proposals. Try again tomorrow.",
    );
    expect(message.compareDocumentPosition(submit()) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(
      0,
    );
    expect(field("Question")).not.toBeInvalid();
    expect(field("Question")).toHaveValue(QUESTION);
  });

  test("an error goes away once the input changes", async () => {
    mockApi({
      [PROPOSE]: apiError(422, "VALIDATION_ERROR", "Trading has to close in the future.", {
        field: "close_at",
      }),
    });
    const { user } = renderWithProviders(<ProposeForm />);
    await fillIn(user);
    await user.click(submit());
    await screen.findByRole("alert");

    await user.click(tier("Medium"));

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(field("Trading closes")).not.toBeInvalid();
  });

  test("without an answer the banner stands above the button, the input stays, and sending again is the user's decision", async () => {
    const api = mockApi({ [PROPOSE]: noAnswer });
    const onSent = vi.fn();
    const { user } = renderWithProviders(<ProposeForm onSent={onSent} />);

    await fillIn(user);
    await user.click(submit());

    expect(await screen.findByRole("alert")).toHaveTextContent("No connection. Try again.");
    expect(
      screen.getByText("If your proposal arrived, it is in the list below."),
    ).toBeInTheDocument();
    expect(field("Question")).toHaveValue(QUESTION);
    expect(api.calls(PROPOSE)).toHaveLength(1);
    expect(onSent).not.toHaveBeenCalled();
    await waitFor(() => expect(submit()).toBeEnabled());

    api.on(PROPOSE, json(201, proposed()));
    await user.click(submit());

    await waitFor(() => expect(onSent).toHaveBeenCalledTimes(1));
    expect(api.calls(PROPOSE)).toHaveLength(2);
  });

  test("a 503 shows the API's sentence in the banner", async () => {
    mockApi({
      [PROPOSE]: apiError(
        503,
        "SERVICE_UNAVAILABLE",
        "This is temporarily unavailable. Try again shortly.",
      ),
    });
    const { user } = renderWithProviders(<ProposeForm />);

    await fillIn(user);
    await user.click(submit());

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This is temporarily unavailable. Try again shortly.",
    );
  });

  test("after RATE_LIMITED the button is disabled for the time the API names, with a countdown", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      mockApi({
        [PROPOSE]: apiError(429, "RATE_LIMITED", "Too many attempts. Try again later.", {
          retry_after_seconds: 20,
        }),
      });
      const { user } = renderWithProviders(<ProposeForm />);

      await fillIn(user);
      await user.click(submit());

      expect(await screen.findByRole("alert")).toHaveTextContent(
        "Too many attempts. Try again later.",
      );
      expect(submit()).toBeDisabled();
      expect(screen.getByText(/^Try again in \d+ s$/)).toBeInTheDocument();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(21_000);
      });

      expect(submit()).toBeEnabled();
    } finally {
      vi.useRealTimers();
    }
  });
});
