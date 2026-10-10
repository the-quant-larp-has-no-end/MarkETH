# Frontend

Next.js 16 (App Router, `output: "standalone"`), React 19, TypeScript 5.9, Tailwind CSS 4, shadcn/ui, TanStack Query, `openapi-fetch`. Node 20.19+ or 24. Run every command from `frontend/`.

```
npm run dev            # next dev; in the stack it sits behind nginx at http://localhost:8080
npm run lint           # eslint, with prettier as a rule; `npm run lint -- --fix` formats
npm run typecheck      # next typegen, then tsc --noEmit
npm test               # vitest run (jsdom, Testing Library)
npm run e2e            # playwright test: the specs of e2e/ in Chrome, against the stack that is up; `make e2e`
npm run build          # next build -> .next/standalone
npm run generate:api   # backend/openapi.json -> src/lib/api/schema.d.ts; use `make api-client`
```

`DESIGN.md` is the design system and the specification of every screen, state, error treatment and word of copy. A screen, component, colour or sentence that is not in it is added there first. Its table "What MarkETH must not look like" is binding.

This is Next.js 16, which differs from older versions in ways that matter here: a page's `params` is a Promise, an error boundary gets `retry`, `priority` on `next/image` is `loading="eager"`, and there is no `next lint`. The documentation of the installed version is in `node_modules/next/dist/docs/`; read the page before using an API of Next.

## What this app is

A client of `/api` on its own origin, and nothing else. Every page is a static shell whose HTML is the boot screen; all data is fetched in the browser. The web server has no identity and cannot reach the API: there is no route handler, server action, `proxy.ts`, server-side fetch or environment variable. Tunables come from `GET /api/config`.

Nobody is signed out. The portal in front of the app signs every visitor in before a page loads. `GET /api/me` either answers or says `PROFILE_REQUIRED`, and then the set-up screen stands in for the route. Do not build a sign-in, a sign-out or a signed-out state. Inside the app `useMe()` and `useConfig()` always return a value.

## Layers

Imports only point downwards. ESLint fails the build otherwise.

| Layer | Contains | Rules |
| --- | --- | --- |
| `src/app` | `layout.tsx`, `providers.tsx`, one `page.tsx` per route, `globals.css` | A page renders one screen from `features/` inside `Suspense` and sets its title. No logic, no fetching. The pages are final: a screen changes in its feature. |
| `src/features/<name>` | One screen or flow: components, hooks, tests | Starts with `"use client"`. Exports through `index.ts`; another feature is imported through its `index.ts` only. |
| `src/components` | The shared components of DESIGN §12, in folders by kind | Know no feature. Data comes in as props; `useConfig()` and `useMe()` are allowed. |
| `src/components/ui` | shadcn/ui primitives, restyled to the tokens | Used through the components above where one exists. |
| `src/lib` | API client, queries, mutations, formatting, hooks, live data | Imports nothing from above. |
| `src/test` | Set-up, API mock, fixtures, fakes, the design-rules test | Imported by tests only. |

## Rules

1. **Amounts are strings.** A `*_cr` or `shares` value is never passed to `Number` or `parseFloat`, never compared, never calculated with. It is shown with `Amount` or the helpers of `lib/format`, and sent back as it arrived. The one thing read from an amount is whether it is the string `0` (`isZero`).
2. **The server owns every number and every decision.** No price, balance, limit, rank, sort order or "can afford" is worked out here: `allowed`, `affordable`, `can_trade`, `can_get_voucher`, `can_settle` and `trading_open` are the answers. If the API does not send it, the screen does without it and the gap is reported.
3. **One door to the API.** Only `lib/api/client.ts` talks to the network. `call()` sends `Accept: application/json` on every request and throws nothing but `ApiError`. A 401 without a `code` is the portal's and reloads the page once; everything with a `code` is the API's and is shown with its `message`, as sent (`messageOf`).
4. **Server state lives in TanStack Query.** Keys are in `lib/api/keys.ts`, every read in `lib/api/queries.ts`, every write in `lib/api/mutations.ts`. No copy of server data in `useState`.
5. **Tunables come from `useConfig()`** or from the response in hand. No limit, preset, length, amount or interval is written into code or copy. Constants that DESIGN.md fixes (durations, breakpoints) are in `lib/design.ts` and `globals.css`, each with its section.
6. **Tokens and listed components only.** No hex value, pixel font size, ad-hoc radius, gradient, blur, `dark:` class or card shadow in a component. Type is set with the `type-*` utilities. Dark theme only. A tap target is at least 44 px.
7. **Four states.** A screen has loading (skeletons shaped like its content), empty, error (a banner with "Try again") and content before it is done. `QueryState` renders loading and error. An error is never shown only as a toast.
8. **Words.** API messages, ledger labels and product names are shown as sent. On screen the unit is the "token", never "CR" or "credits". Tokens are given, a code is redeemed, a voucher is got: "buy", "price" and "order" belong to shares alone, where the trade sheet uses them ("Buy YES", "Average price", "The price moved."). Never "bet", "odds", "top up", "deposit" or "cash out". `status`, `tier`, `side` and the like are keys, not copy: the words for them are in DESIGN.md.
9. **Same origin only.** Fonts through `next/font`, images from `public/brand`, no CDN, no wasm, no worker: the Content-Security-Policy allows none of it.
10. **Nothing is kept in the browser.** A code card's code is read from `?code=` and removed from the address at once. A voucher code lives in memory while its view is open. A trade without an answer is kept in memory. No `localStorage`, `sessionStorage` or cookie; the one exception is the flag of `lib/session-recovery.ts`.
11. **Every link is `Link` from `@/components/shell`.** It is `next/link`, and it drives the route progress line.
12. **One `h1` per screen**, through `Screen`: the title of an inner screen, a hidden one on a main screen, or the screen's own (the question on Market detail).
13. **The hooks lint includes the React Compiler rules.** No `setState` in an effect body, no ref read during render. Derive during render; read browser state with `useSyncExternalStore` (`lib/hooks`).
14. No "v2", "new", "old" or "legacy" wording in code or comments: describe what is.

## What to build with

| For | Use | From |
| --- | --- | --- |
| The frame of a screen, its top bar and `h1`; what stays pinned above the tab bar | `Screen` (`top`, `title`, `backTo`, `width`, `bleed`, `pinned`) | `@/components/shell` |
| Links, the "not found" screen (returned in place of a screen, never inside its `Screen`), hiding the tab bar | `Link`, `NotFoundScreen`, `useHideTabBar` | `@/components/shell` |
| The current user, the tunables | `useMe()`, `useConfig()` | `@/lib/api/session` |
| An amount, a signed amount, a code as text, a figure the API has none of | `Amount`, `SignedAmount`, `CodeText`, `Missing` | `@/components/money` |
| An amount, shares or a percentage inside a sentence | `formatTokens`, `formatShares`, `formatAmount`, `formatPercent`, `formatRate`, `formatRatio`, `formatCount` | `@/lib/format` |
| Dates and times | `DateTime`, `Day`; `formatDateTime`, `formatDeadline` | `@/components/time`, `@/lib/format` |
| A market in a list, the price, the status | `MarketCard`, `MarketCardSkeleton`, `Price`, `StatusBadge`, `Deadline` | `@/components/market` |
| The words for a `status` and a `tier` | `statusName`, `tierName`, `tierExplanation` | `@/components/market` |
| The trade controls | `OutcomeButtons`, `OutcomeToggle`, `StakePresets`, `SellPresets`, `StakeLeftLine`, `QuotePanel` | `@/components/market` |
| Rows of a list | `RowList` with `LedgerRow`, `TradeRow`, `PositionRow`, `ProductRow`, `VoucherRow`, `LeaderboardRow`; `RowSkeletons`, `LeaderboardFigure`, `holdingsText` | `@/components/rows` |
| Loading, error, empty, more pages | `QueryState`, `ErrorBanner`, `Banner`, `InlineError`, `EmptyState`, `LoadMore`, `RetryCountdown`; `Skeleton` | `@/components/feedback`, `@/components/ui/skeleton` |
| What a form shows above its button for an answer that names no field | `SubmitError` (with `placeError(...).form`) | `@/components/feedback` |
| The bean animations, a figure that flashes | `BeanDrop`, `BeanBounce`, `Flash` | `@/components/feedback` |
| A sheet that is a dialog from 768 px | `ResponsiveSheet`, `SheetBody`, `SheetFooter` | `@/components/sheet` |
| Form fields; bringing the first refused field into view | `TextField`, `TextAreaField`, `SelectField`, `DateTimeField`, `CodeInput`, `Field`; `revealFirstInvalid` | `@/components/form` |
| Buy/Sell, filter chips, tabs that are links | `SegmentedControl`, `FilterChips`, `RemovableChip`, `LinkTabs` | `@/components/controls` |
| QR code and camera | `QrCode`, `QrScanner` | `@/components/qr` |
| Logo, token icon, trophies, illustration slots | `Logo`, `TokenIcon`, `Trophy`, `Illustration` | `@/components/brand` |
| Buttons, dialogs, tabs, tables, toasts | `Button`, `Dialog`, `AlertDialog`, `Tabs`, `Table`, `Card`, `Badge`, `Checkbox`, `RadioGroup` (`radioCardClasses` for a radio card); `toast` from `sonner` | `@/components/ui/*` |
| The profile form, the welcome dialog's state | `ProfileForm`, `useWelcomeOpen` | `@/features/session` |
| Media queries, a debounced value, a countdown, the clock | `useMediaQuery`, `usePrefersReducedMotion`, `useDebouncedValue`, `useRetryAfter`, `useSecondsUntil`, `useNow` | `@/lib/hooks` |

Two lengths of the shell are CSS variables, for what has to keep clear of the bars: `--top-bar-space` and `--tab-bar-space` (which is `0px` from 768 px). `bottom-(--tab-bar-space)` is "right above the tab bar".

`ResponsiveSheet` does the focus of DESIGN §15 itself: it notes what had the focus when it opened and gives the focus back to that when it has closed, and below 768 px it takes the focus to the sheet (not to a field, which would slide a keyboard up). A screen opens it by state and does nothing else about focus. `SheetBody` scrolls and its blocks keep their height; `SheetFooter` stays pinned. A surface of its own that puts a title over a `SheetBody` (the admin dialogs) takes `sheetHeaderClasses` and gives its header `pb-2`, as the sheet does: what scrolls is then cut below the title, not at its foot. After a refused submit, a form whose button stands below its fields calls `revealFirstInvalid`.

`Button` decides its own width: as wide as its column below 768 px, as wide as its label from there, also inside a column that stretches its children. `width="full"` (the buttons of a sheet, a pair that shares a row) and `width="fit"` (a button beside text, in a banner or a table) say otherwise. An icon button takes no width.

`Tabs` shows the focus ring for the keyboard only: a tab that was clicked or tapped has none, although the primitive focuses it with a script. The view of a tab is a keyboard stop of its own and keeps its ring.

## Reads and writes

A read: `useQuery(marketQuery(id))`, `useInfiniteQuery(ledgerQuery)`. The options carry the key, the request and the type of the answer. A list that pages sends no page size and asks for the next page with `next_cursor` (`LoadMore`).

A write: `useMutation(redeemCodeMutation)`. Each mutation of `lib/api/mutations.ts` brings what the write does to the cache, so a screen never invalidates by hand. Add what is the screen's own (a toast, closing a sheet) in the callbacks of `mutate(variables, { onSuccess, onError })`, and do not replace the callbacks of the options. Disable the button while `isPending`: after a write without an answer the mutation stays pending until what it would have changed has been read again (DESIGN §14).

Every request goes out when it is made, whatever the browser says about the connection (`createQueryClient`). A read that cannot get through fails and the screen shows "No connection. Try again."; it is asked again by itself when the connection is back. A write that cannot get through fails at once and is never sent later by itself. No screen sets `networkMode` or `retry`.

| After | In the cache |
| --- | --- |
| Save profile | `me` is the answer; leaderboard |
| Trade | balance and market from the answer; the market, its history, everything under `me`, the market lists, leaderboard |
| Redeem a code, get a voucher | balance from the answer; everything under `me`, products, leaderboard |
| Fresh voucher code | nothing; the vouchers when the voucher is used up or gone |
| Propose | the market is the answer, and the first of the caller's proposals; proposals, the market lists |
| Admin action on a market | the market is the answer; its history, the market lists, risk, everything under `me`, leaderboard |
| Create or revoke codes, acknowledge an alert | batches, risk |

Two things change the cache without a write of the screen. A `status` event of the market stream (or a poll that finds another status) marks the market lists out of date, and for a market that was resolved or voided everything under `me` too: a payout or a refund changed the balance in the top bar. And the trade ticket reads `/me` together with the market when it opens, as the Wallet does, because monthly tokens and refunds are booked between visits.

A write the contract gains gets its mutation and its row in that file; `lib/api/contract.test.ts` fails until it has one.

An error is placed with the helpers of `lib/api/errors.ts`, never by digging in `details`: `placeError(error, fields)` for a form, `staleQuote`, `stakeLeft`, `retryAfterSeconds` and `useRetryAfter` for the cases DESIGN §14 names.

## Trades

`newTradeSubmission(marketId, request)` serialises the body once and gives it one Idempotency-Key; `tradeMutation` sends exactly those. The guard is the quote itself: `min_shares` is the quote's `shares`, `min_proceeds_cr` its `amount_cr`.

Both are kept until an answer says what became of the request: a 2xx, or a 4xx other than 429. Then they are dropped and the next Confirm builds another body with another key; `QUOTE_STALE` is such a 4xx, so show `details.quote` and wait for the user. Every other outcome says nothing about the trade (a 5xx, a 429, the portal's 401, no response: `keepsTradeRequest(error)`): the button reads "Try again" and sends the stored body with the stored key again, unchanged. While they are kept the sheet offers nothing else, and closing and reopening it shows the same unanswered trade. They live in memory, not in browser storage.

The ticket's state is in `features/trade/ticket-store.ts`: one state per market and query client, in memory. It is the only caller of `tradeMutation`. A host places `TradeTicket` straight into `ResponsiveSheet` or onto a panel surface: it brings its own `SheetBody` and `SheetFooter`. `features/market-detail/market-detail-with-ticket.test.tsx` runs the real ticket inside the real screen; the other tests of the two features each put a stand-in in place of the other.

## Live data

`useMarketStream(marketId, { enabled, onStatus })` keeps `marketQuery` and `marketHistoryQuery` current from the event stream and drops any event whose `version` is not newer. `onStatus` is called for a `status` event of the market: it is how a screen knows that something happened in front of the user (the bean drop of a payout). When the stream fails it polls every `live_poll_fallback_seconds` and tries the stream again at each poll, with no error on screen; the polls keep that pace also when a stream opens and is cut again at once. The poll is a normal API call, so an ended portal session shows up there and reloads the page. An open quote is never touched by an event. Nothing else is live: a screen reads what it shows when it opens and when the window gets the focus back.

## Tests

- Beside the code: `thing.test.tsx`. `renderWithProviders` (`@/test/render`) gives a query client with the tunables and a user in it; `mockApi` (`@/test/http`) answers `fetch` by contract route and records the requests; fixtures typed by the contract are in `@/test/fixtures`.
- A request without a route fails the test. `json`, `apiError`, `portal401`, `errorPage`, `noAnswer` and `deferred` make the answers.
- The router is in memory (`@/test/navigation`): `navigation.go("/leaderboard?sort=trades")` before rendering, `navigation.href` after. A screen that keeps state in its query string writes it with the browser's History API; `followHistoryWrites()` in `beforeEach` makes the router follow those writes and returns the two spies. The Wallet's tests of `?code=` look at `window.location` instead, and set the address in both places.
- A test starts as a phone with reduced motion: `setViewport("wide")` and `setMediaQuery` (`@/test/media`) change that. `installFakeEventSource` plays stream events; `intersect()` scrolls a list to its end, and `watchScrolling()` (`@/test/observers`) says where an element lies. `sizeCharts()` (`@/test/charts`) gives a Recharts chart a size, without which jsdom draws none.
- A toast is dismissed after every test (`src/test/setup.ts`): sonner keeps its toasts outside React.
- With a clock, use `vi.useFakeTimers({ shouldAdvanceTime: true })` so that `user` and `findBy` keep working, and `vi.useRealTimers()` in a `finally`.
- The cache tells its observers a moment after it changed: after an event or a `setQueryData`, wait with `findBy` or `waitFor` before looking at the screen.
- Assert on what the user sees and on what was sent. An expected amount is the string of the fixture, never arithmetic in the test.
- `src/test/design-rules.test.ts` fails on a forbidden class, colour, word or call in any file under `src/`.

**The browser suite.** `npm run e2e` runs the specs of `e2e/` (`*.spec.ts`) in the Chrome that is installed, each at the projects "phone" (375 × 667) and "desktop" (1024 × 768), one after the other. It starts no stack: it drives the one at `E2E_BASE_URL` (`http://localhost:8080` when left out), which has to be up and seeded. `E2E_IDENTITY=cookies` (when left out) names a person with the cookies `dev_user_id` and `dev_user_name` of the development proxy; `E2E_IDENTITY=headers` sends `X-User-Id` and `X-User-Name` itself, for a stack of `compose.yaml` alone. `e2e/support/runner.ts` lists every variable the runner reads. vitest sees `src/**/*.test.{ts,tsx}` and nothing of `e2e/`.

- A spec imports from `./support` only: `test` with the fixture `people`, `STUDENTS`, `expect`, `expectLayoutHolds` and `screenshot`. `people.as(STUDENTS.anna)` is a browser context of a student of the seed; `people.join(degree?)` makes a person up (`e2e.<run>.<n>@ethz.ch`) who sets up their own profile.
- A spec reads the seeded people and markets and changes only what it made itself, so it passes again on the same database.
- A spec ends with `expectLayoutHolds(page)` and `screenshot(page, testInfo, screen)` for each of its screens: nothing lies over anything else, the page does not scroll sideways, and the picture lies in the directory `E2E_SCREENSHOTS` names (`test-results/screens` when left out) as `<spec>.<screen>.<project>.png`. What the layout check measures and leaves out stands at the top of `e2e/support/layout.ts`.

## Recipes

**Build a screen.** (1) Read its section in `DESIGN.md` §13 and its rows in §14. (2) Work in `src/features/<name>/`; keep the names and props its `index.ts` exports. (3) Start from `<Screen>`, read with the options of `lib/api/queries.ts`, write with those of `lib/api/mutations.ts`. (4) Tests for its four states and for what it sends.

**Use an endpoint that is new or changed.** (1) `make api-client`. (2) A key in `lib/api/keys.ts` and a `queryOptions` in `lib/api/queries.ts` for a read; a `mutationOptions` with its cache handling in `lib/api/mutations.ts` for a write. (3) Types come from `@/lib/api/schema`; never retype a response. (4) A fixture in `src/test/fixtures.ts`.

**Add a shadcn component.** (1) Name it in `DESIGN.md` §12. (2) `npx shadcn@4.21.4 add <name>`. (3) `git diff src/app/globals.css` must be empty. (4) Restyle it: tokens, 48 px controls, the `type-*` utilities, no `dark:`, no shadow.

**Change a colour, font or radius.** `DESIGN.md` §9 and `src/app/globals.css` in the same commit; a test compares the two.

**Put a pixel illustration in.** Add the file to `public/brand/pixel/` and set its slot's `art` in `src/components/brand/illustration.tsx`.

**Handle an error code.** It arrives in the generated `ErrorCode`. Add its row to `DESIGN.md` §14 and treat it where it occurs; read `details` through a helper of `lib/api/errors.ts`.

## Versions that are pinned on purpose

ESLint 9 (the plugins of `eslint-config-next` do not accept 10), TypeScript 5.9 (`openapi-typescript` needs 5), jsdom 29 and `@testing-library/jest-dom` 6.9.1 (later ones need Node 22), Recharts 3.8.0 (what the `chart` component was written for). `redocly.yaml` switches off one validation rule so the event stream's `itemSchema` does not stop type generation. `next.config.ts` sets `agentRules: false`, so that `next dev` writes no rules file of its own beside this one.
