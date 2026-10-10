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
    const { user } = renderWithProviders(<ProfileForm submitLabel="Create profile" />);

    await fillIn(user);
    await user.click(submit());

    expect(await screen.findByRole("alert")).toHaveTextContent("This username is taken.");
    expect(username()).toBeInvalid();
    expect(username()).toHaveAccessibleDescription(/This username is taken\./);
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
            msg: "String should match pattern '^[a-z0-9_]{1,32}$'",
            type: "x",
          },
        ],
      }),
    });
    const { user } = renderWithProviders(<ProfileForm submitLabel="Create profile" />);

    await fillIn(user);
    await user.click(submit());

    await waitFor(() => expect(username()).toBeInvalid());
    expect(username()).toHaveAccessibleDescription(/String should match pattern/);
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
});
