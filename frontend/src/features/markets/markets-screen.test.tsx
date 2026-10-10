import { act, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { marketSummary, me, OTHER_MARKET_ID } from "@/test/fixtures";
import { apiError, deferred, errorPage, json, mockApi, noAnswer, type Route } from "@/test/http";
import { navigation } from "@/test/navigation";
import { renderWithProviders } from "@/test/render";

import { MarketsScreen } from "./index";

const MARKETS: Route = "GET /api/markets";
/** The window getting the focus back also reads the balance of the top bar again. */
const ME: Route = "GET /api/me";

const POLYBAHN = "Will the Polybahn run without interruption during the exam session?";
const MENSA = "Will the Mensa serve Älplermagronen before the end of October?";

const polybahn = () => marketSummary();
const mensa = () =>
  marketSummary({ id: OTHER_MARKET_ID, question: MENSA, p_yes: 0.31, p_no: 0.69, volume_cr: "80" });

const cards = () => screen.getAllByRole("article");
const chip = (name: string) =>
  within(screen.getByRole("group", { name: "Status" })).getByRole("button", { name });

afterEach(() => {
  vi.useRealTimers();
});

describe("MarketsScreen", () => {
  describe("loading", () => {
    test("shows six skeleton cards until the list has answered", async () => {
      const answer = deferred();
      mockApi({ [MARKETS]: answer.handler });

      renderWithProviders(<MarketsScreen />);

      expect(screen.getByRole("status")).toHaveTextContent("Loading markets");
      expect(document.querySelectorAll("[data-slot=market-card-skeleton]")).toHaveLength(6);
      expect(screen.queryByRole("article")).not.toBeInTheDocument();

      await act(async () => answer.resolve(json(200, { items: [polybahn()] })));

      expect(await screen.findByRole("article")).toBeInTheDocument();
      expect(document.querySelector("[data-slot=market-card-skeleton]")).not.toBeInTheDocument();
    });

    test("the chips are there from the start, with Open chosen", () => {
      mockApi({ [MARKETS]: deferred().handler });

      renderWithProviders(<MarketsScreen />);

      expect(chip("Open")).toHaveAttribute("aria-pressed", "true");
      for (const name of ["Closed", "Resolved", "All"]) {
        expect(chip(name)).toHaveAttribute("aria-pressed", "false");
      }
    });
  });

  describe("content", () => {
    test("shows the market cards in the order the API returns them", async () => {
      mockApi({ [MARKETS]: json(200, { items: [mensa(), polybahn()] }) });

      renderWithProviders(<MarketsScreen />);

      await screen.findByRole("heading", { name: MENSA });
      expect(cards().map((card) => within(card).getByRole("heading").textContent)).toEqual([
        MENSA,
        POLYBAHN,
      ]);
    });

    test("is a list of cards: one column on a phone, two from 768 px, three from 1024 px", async () => {
      mockApi({ [MARKETS]: json(200, { items: [polybahn(), mensa()] }) });

      renderWithProviders(<MarketsScreen />);

      const list = await screen.findByRole("list");
      expect(list).toHaveClass("grid", "gap-3", "md:grid-cols-2", "lg:grid-cols-3");
      expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    });

    test("a card shows what the API sent: the percentage, the volume and the deadline", async () => {
      vi.useFakeTimers({ now: new Date("2026-10-11T10:00:00Z"), toFake: ["Date"] });
      mockApi({ [MARKETS]: json(200, { items: [polybahn()] }) });

      renderWithProviders(<MarketsScreen />);

      const card = await screen.findByRole("article");
      expect(within(card).getByText("54.8%")).toBeInTheDocument();
      expect(within(card).getByText("chance")).toBeInTheDocument();
      expect(within(card).getByText("1,250.5")).toBeInTheDocument();
      expect(within(card).getByText("Vol.")).toBeInTheDocument();
      expect(within(card).getByText("Closes in 3 days")).toBeInTheDocument();
    });

    test("an amount stays the string it arrived as: it is cut, never rounded", async () => {
      mockApi({
        [MARKETS]: json(200, { items: [marketSummary({ volume_cr: "1999.999999" })] }),
      });

      renderWithProviders(<MarketsScreen />);

      // As a number it would have shown as 2,000.
      expect(await screen.findByText("1,999.99")).toBeInTheDocument();
      expect(screen.getByText("1,999.99 tokens")).toHaveClass("sr-only");
    });

    test("opens a market from its card, and the trade sheet from Yes and No", async () => {
      mockApi({ [MARKETS]: json(200, { items: [polybahn()] }) });
      const { user } = renderWithProviders(<MarketsScreen />);
      const market = polybahn();

      expect(await screen.findByRole("link", { name: POLYBAHN })).toHaveAttribute(
        "href",
        `/markets/${market.id}`,
      );
      expect(screen.getByRole("link", { name: `Yes: ${POLYBAHN}` })).toHaveAttribute(
        "href",
        `/markets/${market.id}?buy=YES`,
      );

      await user.click(screen.getByRole("link", { name: `No: ${POLYBAHN}` }));
      expect(navigation.href).toBe(`/markets/${market.id}?buy=NO`);
    });

    test("ends with the way to propose a market, as a secondary button", async () => {
      mockApi({ [MARKETS]: json(200, { items: [polybahn()] }) });
      const { user } = renderWithProviders(<MarketsScreen />);

      const propose = await screen.findByRole("link", { name: "Propose a market" });
      expect(propose).toHaveAttribute("data-variant", "secondary");
      // After the list, not before it.
      expect(
        screen.getByRole("list").compareDocumentPosition(propose) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();

      await user.click(propose);
      expect(navigation.pathname).toBe("/propose");
    });

    test("has one h1, which names the screen without showing", async () => {
      mockApi({ [MARKETS]: json(200, { items: [polybahn()] }) });

      renderWithProviders(<MarketsScreen />);
      await screen.findByRole("article");

      const headings = screen.getAllByRole("heading", { level: 1 });
      expect(headings).toHaveLength(1);
      expect(headings[0]).toHaveTextContent("Markets");
      expect(headings[0]).toHaveClass("sr-only");
    });
  });

  describe("the status chips", () => {
    test("Open asks for OPEN and HALTED, so that a paused market stays in sight", async () => {
      const api = mockApi({
        [MARKETS]: json(200, {
          items: [polybahn(), marketSummary({ ...mensa(), status: "HALTED", trading_open: false })],
        }),
      });

      renderWithProviders(<MarketsScreen />);

      await screen.findByRole("heading", { name: MENSA });
      expect(api.calls(MARKETS)).toHaveLength(1);
      expect(api.lastCall(MARKETS)?.query.getAll("status")).toEqual(["OPEN", "HALTED"]);
      // The paused market shows its badge in place of the Yes and No buttons.
      const paused = cards()[1]!;
      expect(within(paused).getByText("Paused")).toBeInTheDocument();
      expect(within(paused).queryByRole("link", { name: /^Yes:/ })).not.toBeInTheDocument();
    });

    test.each([
      ["Closed", ["CLOSED"]],
      ["Resolved", ["RESOLVED", "VOID"]],
      ["All", []],
    ] as const)("%s sends its statuses to the API", async (name, statuses) => {
      const api = mockApi({ [MARKETS]: json(200, { items: [polybahn()] }) });
      const { user } = renderWithProviders(<MarketsScreen />);
      await screen.findByRole("article");

      await user.click(chip(name));

      await waitFor(() => expect(api.calls(MARKETS)).toHaveLength(2));
      expect(api.lastCall(MARKETS)?.query.getAll("status")).toEqual([...statuses]);
      // Nothing else is sent: no page size, no order.
      expect([...new Set(api.lastCall(MARKETS)?.query.keys())]).toEqual(
        statuses.length === 0 ? [] : ["status"],
      );
      expect(chip(name)).toHaveAttribute("aria-pressed", "true");
      expect(chip("Open")).toHaveAttribute("aria-pressed", "false");
    });

    test("shows the skeleton, not the other chip's markets, while its own list loads", async () => {
      const closed = deferred();
      const api = mockApi({ [MARKETS]: json(200, { items: [polybahn()] }) });
      const { user } = renderWithProviders(<MarketsScreen />);
      await screen.findByRole("heading", { name: POLYBAHN });
      api.on(MARKETS, closed.handler);

      await user.click(chip("Closed"));

      expect(screen.queryByRole("heading", { name: POLYBAHN })).not.toBeInTheDocument();
      expect(document.querySelectorAll("[data-slot=market-card-skeleton]")).toHaveLength(6);

      await act(async () =>
        closed.resolve(
          json(200, {
            items: [marketSummary({ ...mensa(), status: "CLOSED", trading_open: false })],
          }),
        ),
      );

      const card = await screen.findByRole("article");
      expect(within(card).getByText("Closed")).toBeInTheDocument();
    });

    test("a resolved market shows its outcome in place of the percentage", async () => {
      const api = mockApi({ [MARKETS]: json(200, { items: [] }) });
      const { user } = renderWithProviders(<MarketsScreen />);
      await screen.findByText(/No markets yet/);
      api.on(
        MARKETS,
        json(200, {
          items: [
            marketSummary({ status: "RESOLVED", outcome: "YES", trading_open: false }),
            marketSummary({ ...mensa(), status: "VOID", outcome: "VOID", trading_open: false }),
          ],
        }),
      );

      await user.click(chip("Resolved"));

      const resolved = await screen.findByText("Resolved YES");
      expect(resolved).toBeInTheDocument();
      expect(within(cards()[0]!).queryByText("chance")).not.toBeInTheDocument();
      expect(within(cards()[1]!).getByText("Voided")).toBeInTheDocument();
    });
  });

  describe("empty", () => {
    test("says so with its campus line and offers to propose a market, once", async () => {
      mockApi({ [MARKETS]: json(200, { items: [] }) });

      renderWithProviders(<MarketsScreen />);

      expect(
        await screen.findByText("No markets yet. Quieter than the HG on a Sunday."),
      ).toBeInTheDocument();
      expect(screen.getAllByRole("link", { name: "Propose a market" })).toHaveLength(1);
      expect(screen.getByRole("link", { name: "Propose a market" })).toHaveAttribute(
        "href",
        "/propose",
      );
      expect(screen.queryByRole("list")).not.toBeInTheDocument();
      // The chips stay, so that another status can be chosen.
      expect(chip("All")).toBeInTheDocument();
    });
  });

  describe("error", () => {
    test("shows the API's message as sent in a banner, and reads again on Try again", async () => {
      const api = mockApi({
        [MARKETS]: apiError(500, "INTERNAL_ERROR", "Something went wrong on our side."),
      });
      const { user } = renderWithProviders(<MarketsScreen />);

      const banner = await screen.findByRole("alert");
      expect(banner).toHaveTextContent("Something went wrong on our side.");
      expect(screen.queryByRole("article")).not.toBeInTheDocument();
      api.on(MARKETS, json(200, { items: [polybahn()] }));

      await user.click(within(banner).getByRole("button", { name: "Try again" }));

      expect(await screen.findByRole("article")).toBeInTheDocument();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(api.calls(MARKETS)).toHaveLength(2);
    });

    test("without a connection it says so", async () => {
      mockApi({ [MARKETS]: noAnswer });

      renderWithProviders(<MarketsScreen />);

      expect(await screen.findByRole("alert")).toHaveTextContent("No connection. Try again.");
    });

    test("an error page of the proxy counts as temporarily unavailable", async () => {
      mockApi({ [MARKETS]: errorPage(502) });

      renderWithProviders(<MarketsScreen />);

      expect(await screen.findByRole("alert")).toHaveTextContent(
        "This is temporarily unavailable. Try again shortly.",
      );
    });

    test("after RATE_LIMITED, Try again waits for the time the API names", async () => {
      mockApi({
        [MARKETS]: apiError(429, "RATE_LIMITED", "Too many requests. Try again shortly.", {
          retry_after_seconds: 12,
        }),
      });

      renderWithProviders(<MarketsScreen />);

      const banner = await screen.findByRole("alert");
      expect(banner).toHaveTextContent("Too many requests. Try again shortly.");
      expect(within(banner).getByRole("button", { name: /Try again in \d+ s/ })).toBeDisabled();
    });

    test("markets that are on screen stay there when reading them again fails", async () => {
      const api = mockApi({
        [MARKETS]: json(200, { items: [polybahn()] }),
        [ME]: json(200, me()),
      });
      renderWithProviders(<MarketsScreen />);
      await screen.findByRole("article");
      api.on(MARKETS, noAnswer);

      act(() => {
        window.dispatchEvent(new Event("visibilitychange"));
      });

      await waitFor(() => expect(api.calls(MARKETS)).toHaveLength(2));
      expect(screen.getByRole("heading", { name: POLYBAHN })).toBeInTheDocument();
    });
  });

  describe("requests", () => {
    test("asks the API on its own origin for JSON", async () => {
      const api = mockApi({ [MARKETS]: json(200, { items: [] }) });

      renderWithProviders(<MarketsScreen />);
      await screen.findByText(/No markets yet/);

      const request = api.lastCall(MARKETS)!;
      expect(request.path).toBe("/api/markets");
      expect(new URL(request.url).origin).toBe(window.location.origin);
      expect(request.headers.get("Accept")).toBe("application/json");
    });

    test("reads the list again when the window gets the focus back", async () => {
      const api = mockApi({
        [MARKETS]: json(200, { items: [polybahn()] }),
        [ME]: json(200, me()),
      });
      renderWithProviders(<MarketsScreen />);
      await screen.findByRole("article");
      api.on(MARKETS, json(200, { items: [polybahn(), mensa()] }));

      act(() => {
        window.dispatchEvent(new Event("visibilitychange"));
      });

      expect(await screen.findByRole("heading", { name: MENSA })).toBeInTheDocument();
      expect(api.calls(MARKETS)).toHaveLength(2);
      expect(api.lastCall(MARKETS)?.query.getAll("status")).toEqual(["OPEN", "HALTED"]);
    });

    test("is not live: it opens no event stream", async () => {
      const opened = vi.fn();
      vi.stubGlobal(
        "EventSource",
        class {
          constructor(url: string) {
            opened(url);
          }
        },
      );
      mockApi({ [MARKETS]: json(200, { items: [polybahn()] }) });

      renderWithProviders(<MarketsScreen />);
      await screen.findByRole("article");

      expect(opened).not.toHaveBeenCalled();
    });
  });
});
