import { act, screen, waitFor } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import { config, me, profileSaved } from "@/test/fixtures";
import { apiError, deferred, json, mockApi, noAnswer, type Route } from "@/test/http";
import { renderWithProviders } from "@/test/render";

import { ProfileForm } from "./profile-form";

const SAVE: Route = "PUT /api/me/profile";

const username = () => screen.getByLabelText("Username");
const level = () => screen.getByLabelText("Level");
const programme = () => screen.getByLabelText("Programme");
const policy = () => screen.getByRole("checkbox", { name: "I accept the usage policy" });
const submit = (name = "Create profile") => screen.getByRole("button", { name });

function options(select: HTMLElement): string[] {
  return Array.from(select.querySelectorAll("option"))
    .filter((option) => !option.disabled)
    .map((option) => option.textContent ?? "");
}

async function fillIn(user: ReturnType<typeof renderWithProviders>["user"]) {
  await user.type(username(), "anna_m");
  await user.selectOptions(level(), "Bachelor");
  await user.selectOptions(programme(), "BSc Computer Science");
}

describe("the three fields", () => {
  test("the username's lengths in the help text come from /config", () => {
    renderWithProviders(<ProfileForm submitLabel="Create profile" />, {
      config: config({ username_min_length: 4, username_max_length: 12 }),
    });

    expect(
      screen.getByText("4 to 12 characters: lower-case letters, digits and underscores."),
    ).toBeInTheDocument();
    expect(username()).toHaveAccessibleDescription(
      "4 to 12 characters: lower-case letters, digits and underscores.",
    );
  });

  test("a capital letter becomes its lower-case letter as it is typed, and the field stops at the longest name", async () => {
    const { user } = renderWithProviders(<ProfileForm submitLabel="Create profile" />, {
      config: config({ username_max_length: 8 }),
    });

    await user.type(username(), "Anna_Muster");

    expect(username()).toHaveValue("anna_mus");
  });

  test("the levels are those of /config, shown by name", () => {
    renderWithProviders(<ProfileForm submitLabel="Create profile" />);

    expect(options(level())).toEqual(["Bachelor", "Master", "Doctorate", "Other"]);
  });

  test("the programmes follow the chosen level, and the field is disabled until a level is chosen", async () => {
    const { user } = renderWithProviders(<ProfileForm submitLabel="Create profile" />);

    expect(programme()).toBeDisabled();

    await user.selectOptions(level(), "Master");

    expect(programme()).toBeEnabled();
    expect(options(programme())).toEqual(["MSc Computer Science", "MSc Data Science"]);
  });

  test("changing the level clears the programme", async () => {
    const { user } = renderWithProviders(<ProfileForm submitLabel="Create profile" />);
    await fillIn(user);
    expect(programme()).toHaveValue("BSc Computer Science");

    await user.selectOptions(level(), "Master");

    expect(programme()).toHaveValue("");
    expect(submit()).toBeDisabled();
  });

  test("the button is disabled until all three are filled", async () => {
    const { user } = renderWithProviders(<ProfileForm submitLabel="Create profile" />);

    expect(submit()).toBeDisabled();
    await user.type(username(), "anna_m");
    expect(submit()).toBeDisabled();
    await user.selectOptions(level(), "Bachelor");
    expect(submit()).toBeDisabled();
    await user.selectOptions(programme(), "BSc Physics");
    expect(submit()).toBeEnabled();
  });

  test("the sentence about what others can see stands above the button", () => {
    renderWithProviders(<ProfileForm submitLabel="Create profile" />);

    expect(
      screen.getByText(
        "Other players can see your username, your degree, your tokens and your trading record.",
      ),
    ).toBeInTheDocument();
  });
});

describe("saving", () => {
  test("sends the level as its key and the programme as its label, and hands on the answer", async () => {
    const saved = profileSaved({ username: "anna_m" });
    const api = mockApi({ [SAVE]: json(200, saved) });
    const onSaved = vi.fn();
    const { user } = renderWithProviders(
      <ProfileForm submitLabel="Create profile" onSaved={onSaved} />,
    );

    await fillIn(user);
    await user.click(submit());

    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(onSaved.mock.calls[0]?.[0]).toEqual(saved);
    expect(api.lastCall(SAVE)?.body).toEqual({
      username: "anna_m",
      degree_level: "BACHELOR",
      degree_programme: "BSc Computer Science",
    });
  });

  test("the button is disabled and shows a spinner until the answer arrives: two taps are one request", async () => {
    const answer = deferred();
    const api = mockApi({ [SAVE]: answer.handler });
    const { user } = renderWithProviders(<ProfileForm submitLabel="Create profile" />);

    await fillIn(user);
    await user.click(submit());
    await user.click(submit());

    expect(submit()).toBeDisabled();
    expect(submit()).toHaveAttribute("aria-busy", "true");
    expect(api.calls(SAVE)).toHaveLength(1);

    answer.resolve(json(200, profileSaved()));
    await waitFor(() => expect(submit()).toBeEnabled());
  });

  test("USERNAME_TAKEN appears on the username field with the API's message; the other fields keep their input", async () => {
    mockApi({
      [SAVE]: apiError(409, "USERNAME_TAKEN", "This username is taken.", { field: "username" }),
    });
    const intoView = vi.spyOn(Element.prototype, "scrollIntoView");
    const { user } = renderWithProviders(<ProfileForm submitLabel="Create profile" />);

    await fillIn(user);
    await user.click(submit());

    expect(await screen.findByRole("alert")).toHaveTextContent("This username is taken.");
    expect(username()).toBeInvalid();
    expect(username()).toHaveAccessibleDescription(/This username is taken\./);
    // The button stands below the fields: the refused one is brought into view and
    // takes the focus, so that a short screen shows the message too.
    await waitFor(() => expect(username()).toHaveFocus());
    expect(intoView.mock.contexts).toContain(username());
    expect(level()).not.toBeInvalid();
    expect(username()).toHaveValue("anna_m");
    expect(level()).toHaveValue("BACHELOR");
    expect(programme()).toHaveValue("BSc Computer Science");
  });

  test("a rule the service checks appears on the field it names", async () => {
    mockApi({
      [SAVE]: apiError(422, "VALIDATION_ERROR", "This programme is not offered at this level.", {
        field: "degree_programme",
      }),
    });
    const { user } = renderWithProviders(<ProfileForm submitLabel="Create profile" />);

    await fillIn(user);
    await user.click(submit());

    await waitFor(() => expect(programme()).toBeInvalid());
    expect(programme()).toHaveAccessibleDescription("This programme is not offered at this level.");
    expect(username()).not.toBeInvalid();
  });

  test("a request that does not match its schema appears on the field its loc ends in", async () => {
    mockApi({
      [SAVE]: apiError(422, "VALIDATION_ERROR", "The request is not valid.", {
        errors: [
          {
            loc: ["body", "username"],
            msg: "Use lower-case letters, digits and underscores only.",
            type: "string_pattern_mismatch",
          },
        ],
      }),
    });
    const { user } = renderWithProviders(<ProfileForm submitLabel="Create profile" />);

    await fillIn(user);
    await user.click(submit());

    // The sentence under the field is the API's, as it stands in `details.errors`.
    await waitFor(() => expect(username()).toBeInvalid());
    expect(username()).toHaveAccessibleDescription(
      /Use lower-case letters, digits and underscores only\./,
    );
    expect(screen.queryByText("The request is not valid.")).not.toBeInTheDocument();
  });

  test("an error goes away once the input it was about changes", async () => {
    mockApi({
      [SAVE]: apiError(409, "USERNAME_TAKEN", "This username is taken.", { field: "username" }),
    });
    const { user } = renderWithProviders(<ProfileForm submitLabel="Create profile" />);
    await fillIn(user);
    await user.click(submit());
    await screen.findByRole("alert");

    await user.type(username(), "2");

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(username()).not.toBeInvalid();
  });

  test("without an answer the banner stands above the button, the input stays, and saving again works", async () => {
    const api = mockApi({ [SAVE]: noAnswer });
    const onSaved = vi.fn();
    const { user } = renderWithProviders(
      <ProfileForm submitLabel="Create profile" onSaved={onSaved} />,
    );

    await fillIn(user);
    await user.click(submit());

    expect(await screen.findByRole("alert")).toHaveTextContent("No connection. Try again.");
    expect(username()).toHaveValue("anna_m");
    expect(username()).not.toBeInvalid();
    expect(submit()).toBeEnabled();

    api.on(SAVE, json(200, profileSaved()));
    await user.click(submit());

    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(api.calls(SAVE)).toHaveLength(2);
    expect(api.calls(SAVE)[1]?.body).toEqual(api.calls(SAVE)[0]?.body);
  });

  test("a 503 shows the API's sentence in the banner", async () => {
    mockApi({
      [SAVE]: apiError(
        503,
        "SERVICE_UNAVAILABLE",
        "This is temporarily unavailable. Try again shortly.",
      ),
    });
    const { user } = renderWithProviders(<ProfileForm submitLabel="Create profile" />);

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
        [SAVE]: apiError(429, "RATE_LIMITED", "Too many attempts. Try again later.", {
          retry_after_seconds: 30,
        }),
      });
      const { user } = renderWithProviders(<ProfileForm submitLabel="Create profile" />);

      await fillIn(user);
      await user.click(submit());

      expect(await screen.findByRole("alert")).toHaveTextContent(
        "Too many attempts. Try again later.",
      );
      expect(submit()).toBeDisabled();
      expect(screen.getByText(/^Try again in \d+ s$/)).toBeInTheDocument();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(31_000);
      });

      expect(submit()).toBeEnabled();
      expect(screen.queryByText(/^Try again in/)).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("creating a profile", () => {
  const create = (props: Partial<Parameters<typeof ProfileForm>[0]> = {}) =>
    renderWithProviders(<ProfileForm creating submitLabel="Create profile" {...props} />);

  test("ends with the usage-policy checkbox, unticked, whose link opens the policy in a tab of its own", () => {
    create();

    expect(policy()).not.toBeChecked();
    const link = screen.getByRole("link", { name: "usage policy" });
    expect(link).toHaveAttribute("href", "/policy");
    expect(link).toHaveAttribute("target", "_blank");
    // The checkbox is a line of the form: after the sentence about what others see,
    // before the button.
    const others = screen.getByText(/Other players can see your username/);
    expect(others.compareDocumentPosition(policy()) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
    expect(policy().compareDocumentPosition(submit()) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(
      0,
    );
  });

  test("the button is disabled until the three fields are filled and the box is ticked", async () => {
    const { user } = create();

    await fillIn(user);
    expect(submit()).toBeDisabled();

    await user.click(policy());
    expect(submit()).toBeEnabled();

    await user.click(policy());
    expect(submit()).toBeDisabled();
  });

  test("the tick alone is not enough", async () => {
    const { user } = create();

    await user.click(policy());

    expect(submit()).toBeDisabled();
  });

  test("the save carries the three fields and policy_accepted: true", async () => {
    const saved = profileSaved({ username: "anna_m" });
    const api = mockApi({ [SAVE]: json(200, saved) });
    const onSaved = vi.fn();
    const { user } = create({ onSaved });

    await fillIn(user);
    await user.click(policy());
    await user.click(submit());

    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(onSaved.mock.calls[0]?.[0]).toEqual(saved);
    expect(api.lastCall(SAVE)?.body).toEqual({
      username: "anna_m",
      degree_level: "BACHELOR",
      degree_programme: "BSc Computer Science",
      policy_accepted: true,
    });
    // A JSON true, not a word for it.
    expect(api.lastCall(SAVE)?.rawBody).toContain('"policy_accepted":true');
  });

  test("a submit that slips past the button sends nothing while the box is not ticked", async () => {
    const api = mockApi({ [SAVE]: json(200, profileSaved()) });
    const { user } = create();
    await fillIn(user);

    act(() => {
      document
        .querySelector("[data-slot=profile-form]")!
        .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(api.calls(SAVE)).toHaveLength(0);
  });

  test("the API's sentence for policy_accepted stands under the checkbox, which takes the focus", async () => {
    mockApi({
      [SAVE]: apiError(422, "VALIDATION_ERROR", "Accept the usage policy to set up your profile.", {
        field: "policy_accepted",
      }),
    });
    const { user } = create();

    await fillIn(user);
    await user.click(policy());
    await user.click(submit());

    const sentence = await screen.findByRole("alert");
    expect(sentence).toHaveTextContent("Accept the usage policy to set up your profile.");
    expect(policy()).toBeInvalid();
    expect(policy()).toHaveAccessibleDescription("Accept the usage policy to set up your profile.");
    // Under the row of the checkbox, and nowhere else: not above the button too.
    expect(policy().closest("[data-slot=checkbox-field]")).toContainElement(sentence);
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    await waitFor(() => expect(policy()).toHaveFocus());
    // The other fields keep their input and are not marked.
    expect(username()).toHaveValue("anna_m");
    expect(username()).not.toBeInvalid();
  });

  test("the sentence goes once the box is changed", async () => {
    mockApi({
      [SAVE]: apiError(422, "VALIDATION_ERROR", "Accept the usage policy to set up your profile.", {
        field: "policy_accepted",
      }),
    });
    const { user } = create();
    await fillIn(user);
    await user.click(policy());
    await user.click(submit());
    await screen.findByRole("alert");

    await user.click(policy());

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(policy()).not.toBeInvalid();
  });

  test("two submits in one moment are one request: the second is stopped before the screen has drawn anything", async () => {
    const answer = deferred();
    const api = mockApi({ [SAVE]: answer.handler });
    const { user } = create();
    await fillIn(user);
    await user.click(policy());
    const form = document.querySelector("[data-slot=profile-form]")!;

    // Enter and a click, or a tap that lands twice: no render lies between the two.
    act(() => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });

    await waitFor(() => expect(submit()).toHaveAttribute("aria-busy", "true"));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(api.calls(SAVE)).toHaveLength(1);

    answer.resolve(json(200, profileSaved()));
    await waitFor(() => expect(submit()).not.toHaveAttribute("aria-busy"));
    expect(api.calls(SAVE)).toHaveLength(1);
  });
});

describe("on the Profile screen", () => {
  const profile = me({
    username: "anna_m",
    degree_level: "MASTER",
    degree_programme: "MSc Data Science",
  });

  test("the fields are filled in and Save is disabled until something changed", async () => {
    const { user } = renderWithProviders(
      <ProfileForm initial={profile} submitLabel="Save" requireChange />,
    );

    expect(username()).toHaveValue("anna_m");
    expect(level()).toHaveValue("MASTER");
    expect(programme()).toHaveValue("MSc Data Science");
    expect(submit("Save")).toBeDisabled();

    await user.type(username(), "x");
    expect(submit("Save")).toBeEnabled();

    await user.type(username(), "{Backspace}");
    expect(submit("Save")).toBeDisabled();
  });

  test("a change of degree alone can be saved", async () => {
    const api = mockApi({ [SAVE]: json(200, profileSaved({ signup_granted: false })) });
    const onSaved = vi.fn();
    const { user } = renderWithProviders(
      <ProfileForm initial={profile} submitLabel="Save" requireChange onSaved={onSaved} />,
    );

    await user.selectOptions(programme(), "MSc Computer Science");
    await user.click(submit("Save"));

    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(api.lastCall(SAVE)?.body).toEqual({
      username: "anna_m",
      degree_level: "MASTER",
      degree_programme: "MSc Computer Science",
    });
  });

  test("there is no checkbox: the policy was accepted when the profile was created, and Save sends the three fields alone", async () => {
    const api = mockApi({ [SAVE]: json(200, profileSaved({ signup_granted: false })) });
    const { user } = renderWithProviders(
      <ProfileForm initial={profile} submitLabel="Save" requireChange />,
    );

    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "usage policy" })).not.toBeInTheDocument();

    await user.type(username(), "2");
    await user.click(submit("Save"));

    await waitFor(() => expect(api.calls(SAVE)).toHaveLength(1));
    expect(api.lastCall(SAVE)?.rawBody).not.toContain("policy_accepted");
  });
});
