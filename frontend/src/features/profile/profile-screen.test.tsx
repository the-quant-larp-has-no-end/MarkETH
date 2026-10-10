import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, test } from "vitest";

import { AppShell } from "@/components/shell";
import { keys } from "@/lib/api/keys";
import type { MeResponse } from "@/lib/api/schema";
import { me, profileSaved } from "@/test/fixtures";
import { apiError, deferred, json, mockApi, noAnswer, type Route } from "@/test/http";
import { navigation } from "@/test/navigation";
import { renderWithProviders } from "@/test/render";

import { ProfileScreen } from "./profile-screen";

const SAVE: Route = "PUT /api/me/profile";

const PROFILE = me({
  username: "anna_m",
  name: "Anna Muster",
  email: "anna.muster@ethz.ch",
  degree_level: "MASTER",
  degree_programme: "MSc Data Science",
});

const card = () => document.querySelector("[data-slot=profile-card]") as HTMLElement;
const username = () => screen.getByLabelText("Username");
const level = () => screen.getByLabelText("Level");
const programme = () => screen.getByLabelText("Programme");
const save = () => screen.getByRole("button", { name: "Save" });

function renderProfile(profile: MeResponse = PROFILE) {
  return renderWithProviders(<ProfileScreen />, { me: profile });
}

beforeEach(() => navigation.go("/profile"));

describe("what the screen shows", () => {
  test("an inner screen: a back arrow, and the title as its one h1", () => {
    renderProfile();

    expect(screen.getByRole("button", { name: "Back" })).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Profile");
  });

  test("the card names the username and the degree, and under them the name and the address of the sign-in", () => {
    renderProfile();

    const shown = within(card());
    expect(shown.getByText("anna_m")).toHaveClass("type-h2");
    expect(shown.getByText("MSc Data Science")).toHaveClass(
      "type-body-sm",
      "text-muted-foreground",
    );
    expect(shown.getByText("Anna Muster")).toBeInTheDocument();
    expect(shown.getByText("anna.muster@ethz.ch")).toBeInTheDocument();
    expect(
      shown.getByText("From your ETH sign-in. Only you and VIS see these."),
    ).toBeInTheDocument();
  });

  test("an empty name is left out", () => {
    renderProfile({ ...PROFILE, name: "" });

    const shown = within(card());
    expect(shown.queryByText("Name")).not.toBeInTheDocument();
    expect(shown.getByText("anna.muster@ethz.ch")).toBeInTheDocument();
    expect(card().querySelectorAll("dd")).toHaveLength(3);
  });

  test("the three fields of the set-up are filled in, with the sentence about what others can see", () => {
    renderProfile();

    expect(username()).toHaveValue("anna_m");
    expect(level()).toHaveValue("MASTER");
    expect(programme()).toHaveValue("MSc Data Science");
    expect(
      screen.getByText(
        "Other players can see your username, your degree, your tokens and your trading record.",
      ),
    ).toBeInTheDocument();
  });

  test("Save is the one primary button, disabled until something changed", async () => {
    const { user } = renderProfile();

    expect(save()).toHaveAttribute("data-variant", "primary");
    expect(document.querySelectorAll("[data-slot=button][data-variant=primary]")).toHaveLength(1);
    expect(save()).toBeDisabled();

    await user.type(username(), "2");
    expect(save()).toBeEnabled();

    await user.type(username(), "{Backspace}");
    expect(save()).toBeDisabled();
  });

  test("it reads nothing: the profile is the one the app already holds", () => {
    const api = mockApi();

    renderProfile();

    expect(api.requests).toHaveLength(0);
  });

  test("there is nothing to sign out of, and no welcome", () => {
    renderProfile();

    expect(screen.queryByRole("button", { name: /sign out|log out/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("saving", () => {
  test("sends the three fields, says Profile saved, stays on the screen and shows the saved profile", async () => {
    const saved = profileSaved({
      ...PROFILE,
      username: "anna_m2",
      degree_programme: "MSc Computer Science",
      signup_granted: false,
    });
    const api = mockApi({ [SAVE]: json(200, saved) });
    const { user, client } = renderWithProviders(
      <AppShell>
        <ProfileScreen />
      </AppShell>,
      { me: PROFILE },
    );

    await user.type(username(), "2");
    await user.selectOptions(programme(), "MSc Computer Science");
    await user.click(save());

    expect(await screen.findByText("Profile saved")).toBeInTheDocument();
    expect(api.lastCall(SAVE)?.body).toEqual({
      username: "anna_m2",
      degree_level: "MASTER",
      degree_programme: "MSc Computer Science",
    });
    expect(navigation.href).toBe("/profile");
    expect(navigation.router.push).not.toHaveBeenCalled();
    expect(navigation.router.back).not.toHaveBeenCalled();

    // The answer is the profile now: the card shows it, and there is nothing left to save.
    await waitFor(() => expect(within(card()).getByText("anna_m2")).toBeInTheDocument());
    expect(within(card()).getByText("MSc Computer Science")).toBeInTheDocument();
    expect(client.getQueryData<MeResponse>(keys.me)?.username).toBe("anna_m2");
    expect(username()).toHaveValue("anna_m2");
    expect(save()).toBeDisabled();
    // A profile that existed gets no welcome, whatever the answer says about tokens.
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  test("the balance the answer carries is the balance: nothing is worked out", async () => {
    mockApi({
      [SAVE]: json(
        200,
        profileSaved({ ...PROFILE, username: "anna_m2", balance_cr: "512.345678" }),
      ),
    });
    const { user, client } = renderProfile({ ...PROFILE, balance_cr: "240" });

    await user.type(username(), "2");
    await user.click(save());

    await screen.findByText("Profile saved");
    expect(client.getQueryData<MeResponse>(keys.me)?.balance_cr).toBe("512.345678");
  });

  test("the button waits for the answer: a second tap sends nothing", async () => {
    const answer = deferred();
    const api = mockApi({ [SAVE]: answer.handler });
    const { user } = renderProfile();

    await user.type(username(), "2");
    await user.click(save());
    await user.click(save());
    await user.keyboard("{Enter}");

    expect(save()).toBeDisabled();
    expect(save()).toHaveAttribute("aria-busy", "true");
    expect(api.calls(SAVE)).toHaveLength(1);

    answer.resolve(json(200, profileSaved({ ...PROFILE, username: "anna_m2" })));
    expect(await screen.findByText("Profile saved")).toBeInTheDocument();
    expect(api.calls(SAVE)).toHaveLength(1);
  });

  test("USERNAME_TAKEN appears on the username field with the API's message; the other fields keep their input", async () => {
    mockApi({
      [SAVE]: apiError(409, "USERNAME_TAKEN", "This username is taken.", { field: "username" }),
    });
    const { user } = renderProfile();

    await user.clear(username());
    await user.type(username(), "polyterrasse");
    await user.selectOptions(programme(), "MSc Computer Science");
    await user.click(save());

    expect(await screen.findByRole("alert")).toHaveTextContent("This username is taken.");
    expect(username()).toBeInvalid();
    expect(username()).toHaveAccessibleDescription(/This username is taken\./);
    expect(username()).toHaveValue("polyterrasse");
    expect(level()).toHaveValue("MASTER");
    expect(programme()).toHaveValue("MSc Computer Science");
    expect(level()).not.toBeInvalid();
    expect(programme()).not.toBeInvalid();
    // Nothing was saved: the card still shows the profile as it is, and no toast claims otherwise.
    expect(within(card()).getByText("anna_m")).toBeInTheDocument();
    expect(screen.queryByText("Profile saved")).not.toBeInTheDocument();
  });

  test("a VALIDATION_ERROR the service raises appears on the field it names", async () => {
    mockApi({
      [SAVE]: apiError(422, "VALIDATION_ERROR", "This username is reserved.", {
        field: "username",
      }),
    });
    const { user } = renderProfile();

    await user.clear(username());
    await user.type(username(), "vis");
    await user.click(save());

    await waitFor(() => expect(username()).toBeInvalid());
    expect(username()).toHaveAccessibleDescription(/This username is reserved\./);
  });

  test("a programme that is not on the list is marked on the programme field", async () => {
    mockApi({
      [SAVE]: apiError(422, "VALIDATION_ERROR", "The request is not valid.", {
        errors: [
          { loc: ["body", "degree_programme"], msg: "This programme is not offered.", type: "x" },
        ],
      }),
    });
    const { user } = renderProfile();

    await user.selectOptions(programme(), "MSc Computer Science");
    await user.click(save());

    await waitFor(() => expect(programme()).toBeInvalid());
    expect(programme()).toHaveAccessibleDescription("This programme is not offered.");
    expect(username()).not.toBeInvalid();
  });

  test("without an answer the banner stands above the button, the input stays, and saving again is the same request", async () => {
    const api = mockApi({ [SAVE]: noAnswer });
    const { user } = renderProfile();

    await user.type(username(), "2");
    await user.click(save());

    expect(await screen.findByRole("alert")).toHaveTextContent("No connection. Try again.");
    expect(username()).toHaveValue("anna_m2");
    expect(save()).toBeEnabled();
    expect(screen.queryByText("Profile saved")).not.toBeInTheDocument();

    api.on(SAVE, json(200, profileSaved({ ...PROFILE, username: "anna_m2" })));
    await user.click(save());

    expect(await screen.findByText("Profile saved")).toBeInTheDocument();
    expect(api.calls(SAVE)).toHaveLength(2);
    expect(api.calls(SAVE)[1]?.rawBody).toBe(api.calls(SAVE)[0]?.rawBody);
  });

  test("a 503 shows the API's sentence in the banner and keeps the input", async () => {
    mockApi({
      [SAVE]: apiError(
        503,
        "SERVICE_UNAVAILABLE",
        "This is temporarily unavailable. Try again shortly.",
      ),
    });
    const { user } = renderProfile();

    await user.type(username(), "2");
    await user.click(save());

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This is temporarily unavailable. Try again shortly.",
    );
    expect(username()).toHaveValue("anna_m2");
  });

  test("FORBIDDEN shows the API's message, and the app asks who is calling", async () => {
    const api = mockApi({
      [SAVE]: apiError(403, "FORBIDDEN", "Your account is suspended."),
      "GET /api/me": apiError(403, "FORBIDDEN", "Your account is suspended."),
    });
    const { user } = renderProfile();

    await user.type(username(), "2");
    await user.click(save());

    expect(await screen.findByRole("alert")).toHaveTextContent("Your account is suspended.");
    // Start-up reads that answer of /me and puts the full-screen message in the screen's place.
    await waitFor(() => expect(api.calls("GET /api/me")).toHaveLength(1));
  });
});
