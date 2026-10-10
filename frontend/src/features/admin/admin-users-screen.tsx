"use client";

import { keepPreviousData, useInfiniteQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { LoadMore, QueryState } from "@/components/feedback";
import { Field, TextField } from "@/components/form";
import { Amount, Missing } from "@/components/money";
import { Screen } from "@/components/shell";
import { DateTime } from "@/components/time";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import type { AdminUsersParams } from "@/lib/api/keys";
import { adminUsersQuery } from "@/lib/api/queries";
import type { AdminUserItem, UserRole, UserStatus } from "@/lib/api/schema";
import { useMe } from "@/lib/api/session";
import { SEARCH_DELAY_MS } from "@/lib/design";
import { useDebouncedValue } from "@/lib/hooks";

import { AdminTabs } from "./admin-tabs";
import { type Column, DataTable, DataTableSkeleton, EmptyLine } from "./data-table";
import { UserDialog, UserRoleText, UserStatusText } from "./user-dialog";
import { roleName, ROLES, USER_STATUSES, userStatusName } from "./words";

/** The first option of a select: it sends nothing. */
const ALL = "";

/** The six columns of DESIGN.md §13. The first two hold two lines each, so the table fits 1024 px. */
function columns(ownId: string): readonly Column<AdminUserItem>[] {
  return [
    {
      header: "Player",
      clip: true,
      cell: (user) => (
        <>
          <span className="block w-full truncate">
            {user.username}
            {user.id === ownId && (
              <span className="ml-2 type-caption text-muted-foreground">You</span>
            )}
          </span>{" "}
          <span className="block w-full truncate type-caption text-muted-foreground">
            {user.degree_programme}
          </span>
        </>
      ),
    },
    {
      header: "ETH sign-in",
      clip: true,
      cell: (user) => (
        <>
          <span className="block truncate">{user.name === "" ? <Missing /> : user.name}</span>{" "}
          <span className="block truncate type-caption text-muted-foreground">{user.email}</span>
        </>
      ),
    },
    { header: "Role", cell: (user) => <UserRoleText user={user} /> },
    { header: "Status", cell: (user) => <UserStatusText user={user} /> },
    { header: "Tokens", numeric: true, cell: (user) => <Amount value={user.balance_cr} /> },
    { header: "Joined", cell: (user) => <DateTime value={user.created_at} /> },
  ];
}

/**
 * Admin: users (`/admin/users`, DESIGN.md §13). Everyone with a profile, from `GET
 * /admin/users`. The server searches, filters and pages: the screen sends the choice and
 * shows the rows in the order they arrive.
 *
 * Above the table stand a search field and two selects. Each is a query parameter: the
 * search text is `q`, and an empty field sends none; the selects are `role` and `status`,
 * and their first option sends nothing. A select asks as soon as it is changed, the
 * search a moment after the last keystroke. More rows load with `next_cursor` when the
 * admin reaches the end of the table.
 *
 * A row opens the dialog of its user, which offers what the admin may do to them. The
 * admin's own row, the one whose `id` is the `id` of `/me`, opens nothing: an admin does
 * none of this to their own account.
 */
export function AdminUsersScreen() {
  const me = useMe();
  const [search, setSearch] = useState("");
  const [role, setRole] = useState<UserRole | typeof ALL>(ALL);
  const [status, setStatus] = useState<UserStatus | typeof ALL>(ALL);
  const q = useDebouncedValue(search.trim(), SEARCH_DELAY_MS);

  const params: AdminUsersParams = {};
  if (q !== "") params.q = q;
  if (role !== ALL) params.role = role;
  if (status !== ALL) params.status = status;
  // While another choice is on its way the rows of the last one stay on screen.
  const users = useInfiniteQuery({ ...adminUsersQuery(params), placeholderData: keepPreviousData });

  // `round` gives every opening a dialog of its own, with no step taken and no answer in it.
  const [opened, setOpened] = useState<{ user: AdminUserItem; round: number } | null>(null);
  const [open, setOpen] = useState(false);
  const opener = useRef<HTMLButtonElement | null>(null);
  const searchField = useRef<HTMLDivElement>(null);

  // The rows of every page that was read, in the order they arrived.
  const rows = users.data?.pages.flatMap((page) => page.items) ?? [];
  // The dialog shows the user as the table has them; a row that a filter keeps out of the
  // list is shown as it was when it was opened.
  const shown =
    opened === null ? null : (rows.find((user) => user.id === opened.user.id) ?? opened.user);
  const tableColumns = columns(me.id);

  return (
    <Screen top="main" title="Users">
      <AdminTabs />

      <div data-slot="user-filters" className="flex flex-col gap-3 md:flex-row md:items-end">
        <div ref={searchField} className="md:min-w-0 md:flex-1">
          <TextField
            label="Search"
            name="q"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Username, name or email"
            inputMode="search"
            enterKeyHint="search"
            autoComplete="off"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
          />
        </div>
        <Field label="Role" className="md:w-44">
          {(control) => (
            <NativeSelect
              {...control}
              name="role"
              value={role}
              onChange={(event) => setRole(event.target.value as UserRole | typeof ALL)}
            >
              <NativeSelectOption value={ALL}>All roles</NativeSelectOption>
              {ROLES.map((entry) => (
                <NativeSelectOption key={entry} value={entry}>
                  {roleName(entry)}
                </NativeSelectOption>
              ))}
            </NativeSelect>
          )}
        </Field>
        <Field label="Status" className="md:w-44">
          {(control) => (
            <NativeSelect
              {...control}
              name="status"
              value={status}
              onChange={(event) => setStatus(event.target.value as UserStatus | typeof ALL)}
            >
              <NativeSelectOption value={ALL}>All statuses</NativeSelectOption>
              {USER_STATUSES.map((entry) => (
                <NativeSelectOption key={entry} value={entry}>
                  {userStatusName(entry)}
                </NativeSelectOption>
              ))}
            </NativeSelect>
          )}
        </Field>
      </div>

      <section aria-label="Users" aria-busy={users.isPlaceholderData} className="flex flex-col">
        <QueryState query={users} skeleton={<DataTableSkeleton columns={tableColumns} />}>
          {() =>
            rows.length === 0 ? (
              <EmptyLine>No users match.</EmptyLine>
            ) : (
              <>
                <DataTable
                  label="Users"
                  columns={tableColumns}
                  rows={rows}
                  rowKey={(user) => user.id}
                  canOpen={(user) => user.id !== me.id}
                  onRowOpen={(user, button) => {
                    opener.current = button;
                    setOpened((last) => ({ user, round: (last?.round ?? 0) + 1 }));
                    setOpen(true);
                  }}
                />
                {/* The rows of the last choice have no next page to ask for. */}
                {!users.isPlaceholderData && <LoadMore query={users} />}
              </>
            )
          }
        </QueryState>
      </section>

      {opened !== null && shown !== null && (
        <UserDialog
          key={opened.round}
          user={shown}
          open={open}
          onClose={() => setOpen(false)}
          onCloseAutoFocus={(event) => {
            // The focus goes back to the row that was opened, or to the search when a
            // filter has taken that row off the list.
            event.preventDefault();
            const from = opener.current;
            const back = from !== null && from.isConnected;
            (back ? from : searchField.current?.querySelector("input"))?.focus();
          }}
        />
      )}
    </Screen>
  );
}
