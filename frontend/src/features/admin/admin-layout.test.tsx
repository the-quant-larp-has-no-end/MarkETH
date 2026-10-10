import { screen, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { me } from "@/test/fixtures";
import { setViewport } from "@/test/media";
import { navigation } from "@/test/navigation";
import { renderWithProviders } from "@/test/render";

import { AdminLayout } from "./admin-layout";
import { AdminTabs } from "./admin-tabs";

describe("the admin area is for role ADMIN", () => {
  test("an admin sees the screen", () => {
    renderWithProviders(
      <AdminLayout>
        <p>The admin screen</p>
      </AdminLayout>,
      { me: me({ role: "ADMIN" }) },
    );

    expect(screen.getByText("The admin screen")).toBeInTheDocument();
  });

  test.each(["USER", "BARISTA"] as const)(
    "a %s sees the not-found screen, not a permission lecture",
    (role) => {
      renderWithProviders(
        <AdminLayout>
          <p>The admin screen</p>
        </AdminLayout>,
        { me: me({ role }) },
      );

      expect(screen.queryByText("The admin screen")).not.toBeInTheDocument();
      expect(
        screen.getByText("This page does not exist. Not even in the HG basement."),
      ).toBeInTheDocument();
      expect(screen.queryByText(/allowed|permission|admin/i)).not.toBeInTheDocument();
    },
  );
});

describe("the tabs of the admin area", () => {
  test("are four links, each to its route, with the current one marked", () => {
    navigation.go("/admin/codes");
    renderWithProviders(<AdminTabs />, { me: me({ role: "ADMIN" }) });

    const tabs = screen.getByRole("navigation", { name: "Admin" });
    const links = within(tabs).getAllByRole("link");
    expect(links.map((link) => [link.textContent, link.getAttribute("href")])).toEqual([
      ["Markets", "/admin"],
      ["Codes", "/admin/codes"],
      ["Risk", "/admin/risk"],
      ["Users", "/admin/users"],
    ]);
    expect(screen.getByRole("link", { name: "Codes" })).toHaveAttribute("aria-current", "page");
    for (const other of ["Markets", "Risk", "Users"]) {
      expect(screen.getByRole("link", { name: other })).not.toHaveAttribute("aria-current");
    }
  });

  test("the fourth tab leads to the users", async () => {
    navigation.go("/admin");
    const { user } = renderWithProviders(<AdminTabs />, { me: me({ role: "ADMIN" }) });

    await user.click(screen.getByRole("link", { name: "Users" }));

    expect(navigation.pathname).toBe("/admin/users");
    expect(screen.getByRole("link", { name: "Users" })).toHaveAttribute("aria-current", "page");
  });

  test("on a phone the link to the barista scanner has a line of its own under the tabs, in full view", async () => {
    navigation.go("/admin");
    const { user } = renderWithProviders(<AdminTabs />, { me: me({ role: "ADMIN" }) });

    const tabs = screen.getByRole("navigation", { name: "Admin" });
    const scan = screen.getByRole("link", { name: "Scan vouchers" });
    expect(tabs).not.toContainElement(scan);
    expect(tabs.compareDocumentPosition(scan) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
    // A tap target of 44 px.
    expect(scan).toHaveClass("h-11");
    await user.click(scan);

    expect(navigation.pathname).toBe("/barista");
  });

  test("from 768 px the link sits beside the tabs", async () => {
    setViewport("wide");
    navigation.go("/admin");
    const { user } = renderWithProviders(<AdminTabs />, { me: me({ role: "ADMIN" }) });

    const tabs = screen.getByRole("navigation", { name: "Admin" });
    const scan = within(tabs).getByRole("link", { name: "Scan vouchers" });
    expect(screen.getAllByRole("link", { name: "Scan vouchers" })).toHaveLength(1);
    expect(scan).toHaveClass("h-11");
    await user.click(scan);

    expect(navigation.pathname).toBe("/barista");
  });
});
