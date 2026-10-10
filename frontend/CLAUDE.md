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
| `src/lib` | API client, queries, mutations, the two ways a write is sent, live data, formatting, hooks | Imports nothing from above. |
| `src/test` | Set-up, API mock, fixtures, fakes, the toasts between tests, the design-rules test | Imported by tests only. |
| `e2e` | The browser suite: `*.spec.ts` and `support/` | Run by Playwright alone; vitest sees nothing of it. A spec imports from `./support` only. |

## Rules

1. **Amounts are strings.** A `*_cr` or `shares` value is never passed to `Number` or `parseFloat`, never compared, never calculated with. It is shown with `Amount` or the helpers of `lib/format`, and sent back as it arrived. The one thing read from an amount is whether it is the string `0` (`isZero`). What the user types (a number of shares, an amount, a bound of a filter) is a string too and is sent with the characters that were typed (`typedWhole`, `typedDecimal`).
2. **The server owns every number and every decision.** No price, balance, limit, rank, sort order or "can afford" is worked out here: `allowed`, `affordable`, `can_trade`, `can_get_voucher`, `can_settle`, `can_revoke` and `trading_open` are the answers, and a number of shares that was typed is judged by the quote the server gives for it. If the API does not send it, the screen does without it and the gap is reported.
3. **One door to the API.** Only `lib/api/client.ts` talks to the network. `call()` sends `Accept: application/json` on every request and throws nothing but `ApiError`. A 401 without a `code` is the portal's and reloads the page once; everything with a `code` is the API's and is shown with its `message`, as sent (`messageOf`). The event stream is the one address beside it: `/api/stream` is named in `lib/live/client.ts` and nowhere else.
4. **Server state lives in TanStack Query.** Keys are in `lib/api/keys.ts`, every read in `lib/api/queries.ts`, every write in `lib/api/mutations.ts`. No copy of server data in `useState`. A write is sent through `useSubmit` or a kept submission, never through `useMutation` (below).
5. **Tunables come from `useConfig()`** or from the response in hand. No limit, shortcut, length, amount or interval is written into code or copy. Constants that DESIGN.md fixes (durations, breakpoints, the delay of a search, the quiet zone of a QR code, the pace of a spoken figure) are in `lib/design.ts` and `globals.css`, each with its section.
6. **Tokens and listed components only.** No hex value, pixel font size, ad-hoc radius, gradient, blur, `dark:` class or card shadow in a component. Type is set with the `type-*` utilities. Dark theme only. A tap target is at least 44 px.
7. **Four states.** A screen has loading (skeletons shaped like its content), empty, error (a banner with "Try again") and content before it is done. `QueryState` renders loading and error. An error is never shown only as a toast.
8. **Words.** API messages, ledger labels and product names are shown as sent. On screen the unit is the "token", never "CR" or "credits". Tokens are given, a code is redeemed, a voucher is got: "buy", "price" and "order" belong to shares alone, where the trade sheet uses them ("Buy YES", "Price per share", "The price moved."). Never "bet", "odds", "top up", "deposit" or "cash out". `status`, `tier`, `side`, `kind` and the like are keys, not copy: the words for them are in DESIGN.md.
9. **Same origin only.** Fonts through `next/font`, images from `public/brand`, no CDN, no wasm, no worker: the Content-Security-Policy allows none of it. A brand image on screen is shown through `components/brand`, which is what makes the browser fetch each file once per page session (DESIGN §8); the print sheet draws its own on paper. `public/` itself holds the web manifest and three copies of brand files that a browser asks for at the root of the site (`favicon.ico`, `apple-touch-icon.png`, `apple-touch-icon-precomposed.png`, DESIGN §3).
10. **Nothing is kept in the browser.** A code card's code is read from `?code=` and removed from the address at once. No `localStorage`, `sessionStorage` or cookie; the one exception is the flag of `lib/session-recovery.ts`. What has to outlive a component lives in memory, by the page's query client, and is gone with a reload: a trade and an adjustment of tokens without an answer (`lib/api/submissions.ts`), and the notices this page has told. What belongs to one open surface lives in that surface and is let go of when it closes: a voucher code while its view is open, the codes of a batch while their dialog is open.
11. **Every link is `Link` from `@/components/shell`.** It is `next/link`, and it drives the route progress line. A link that is text is `TextLink`.
12. **One `h1` per screen**, through `Screen`: the title of an inner screen, a hidden one on a main screen, or the screen's own (the question on Market detail).
13. **The hooks lint includes the React Compiler rules.** No `setState` in an effect body, no ref read during render. Derive during render; read browser state with `useSyncExternalStore` (`lib/hooks`).
14. No "v2", "new", "old" or "legacy" wording in code or comments: describe what is.

## What to build with

| For | Use | From |
| --- | --- | --- |
| The frame of a screen, its top bar and `h1`; what stays pinned above the tab bar | `Screen` (`top`, `title`, `backTo`, `width`, `bleed`, `pinned`) | `@/components/shell` |
| Links, the "not found" screen (returned in place of a screen, never inside its `Screen`), hiding the tab bar | `Link`, `TextLink`, `NotFoundScreen`, `useHideTabBar` | `@/components/shell` |
| The current user, the tunables | `useMe()`, `useConfig()` | `@/lib/api/session` |
| A write a button or a form sends; a write that keeps its body and its key | `useSubmit`; `submissionStore`, `useSubmissions` | `@/lib/api/submit`, `@/lib/api/submissions` |
| Following what the server pushes | `useLiveTopic`, `useLiveEvent`, `useLive`, `liveTopic` | `@/lib/live` |
| An amount, a signed amount, a code as text, a figure the API has none of | `Amount`, `SignedAmount`, `CodeText`, `Missing` | `@/components/money` |
| An amount, shares, a price per share or a percentage inside a sentence | `formatTokens`, `formatShares`, `shareWord`, `formatAmount`, `formatAveragePrice`, `formatPercent`, `formatRate`, `formatRatio`, `formatCount` | `@/lib/format` |
| What a field keeps of what is typed | `typedWhole`, `typedDecimal` | `@/lib/format` |
| Dates and times | `DateTime`, `Day`; `formatDateTime`, `formatDeadline` | `@/components/time`, `@/lib/format` |
| A market in a list, the price, the status | `MarketCard`, `MarketCardSkeleton`, `Price`, `StatusBadge`, `Deadline` | `@/components/market` |
| The words for a `status` and a `tier`; whether a market is a proposal (PROPOSED, REJECTED, EXPIRED) | `statusName`, `tierName`, `isProposal` | `@/components/market` |
| Asking again when a market on screen reaches its `close_at` | `useAskAtClose(closeTimes, ask)` | `@/components/market` |
| The trade controls | `OutcomeButtons`, `OutcomeToggle`, `SharesEntry`, `SharePresets`, `SellPresets`, `StakeLeftLine`, `QuotePanel` | `@/components/market` |
| Rows of a list | `RowList` with `LedgerRow`, `TradeRow`, `PositionRow`, `ProductRow`, `VoucherRow`, `LeaderboardRow` (`pinned`, `onSelect`); `RowSkeletons`, `LeaderboardFigure` (`wrap`), `holdingsText`; for a row of a screen's own `RowItem`, `RowText`, `RowFigure` | `@/components/rows` |
| Loading, error, empty, more pages | `QueryState`, `ErrorBanner`, `Banner`, `InlineError`, `EmptyState`, `LoadMore`, `RetryCountdown`; `Skeleton` | `@/components/feedback`, `@/components/ui/skeleton` |
| What a form shows above its button for an answer that names no field | `SubmitError` (with `placeError(...).form`) | `@/components/feedback` |
| The bean animations; a figure that flashes when it changes by itself | `BeanDrop`, `BeanBounce`; `Flash` (`watch`) | `@/components/feedback` |
| A sheet that is a dialog from 768 px | `ResponsiveSheet`, `SheetBody`, `SheetFooter`, `sheetHeaderClasses` | `@/components/sheet` |
| Form fields; bringing the first refused field into view | `TextField`, `TextAreaField`, `SelectField`, `PickerField`, `DateTimeField`, `AmountField`, `CodeInput`, `CheckboxField`, `Field`; `revealFirstInvalid` | `@/components/form` |
| Buy/Sell, filter chips, tabs that are links | `SegmentedControl`, `FilterChips`, `RemovableChip`, `LinkTabs` | `@/components/controls` |
| QR code and camera | `QrCode`, `QrScanner` | `@/components/qr` |
| Logo, token icon, trophies, illustration slots | `Logo`, `LogoMark`, `TokenIcon`, `Trophy`, `Illustration` | `@/components/brand` |
| Buttons, dialogs, tabs, tables, toasts | `Button`, `Dialog`, `AlertDialog`, `Tabs`, `Table`, `Card`, `Badge`, `Checkbox`, `RadioGroup` (`radioCardClasses` for a radio card); `toast` from `sonner` | `@/components/ui/*` |
| The profile form, the welcome dialog's state | `ProfileForm`, `useWelcomeOpen` | `@/features/session` |
| Media queries, a debounced or throttled value, a countdown, the clock | `useMediaQuery`, `usePrefersReducedMotion`, `useDebouncedValue`, `useThrottledValue`, `useRetryAfter`, `useSecondsUntil`, `useNow` | `@/lib/hooks` |
| Keeping the pressed chip or the active tab of a strip in sight | `keepInSight` | `@/lib/keep-in-sight` |

Two lengths of the shell are CSS variables, for what has to keep clear of the bars: `--top-bar-space` and `--tab-bar-space` (which is `0px` from 768 px). `bottom-(--tab-bar-space)` is "right above the tab bar". `Screen`'s `pinned` is as wide as the content column (the outcome buttons of Market detail); a bar across the whole window (the viewer's row of the Leaderboard) is the screen's own.

`ResponsiveSheet` does the focus of DESIGN §15 itself: it notes what had the focus when it opened and gives the focus back to that when it has closed, and below 768 px it takes the focus to the sheet (not to a field, which would slide a keyboard up). A screen opens it by state and does nothing else about focus. `SheetBody` scrolls and its blocks keep their height; `SheetFooter` stays pinned. `header` is the one line of a surface whose title is for screen readers only (the question on the trade sheet), `dismissible={false}` holds every way out while a request the surface sent is out, and `dialog` makes a dialog at every width. A sheet inside a sheet (the picker in the filter sheet) knows it by a context and nests its drawer below 768 px; closing it leaves the one under it open. A surface of its own that puts a title over a `SheetBody` (the admin dialogs) takes `sheetHeaderClasses` and gives its header `pb-2`, as the sheet does: what scrolls is then cut below the title, not at its foot. After a refused submit, a form whose button stands below its fields calls `revealFirstInvalid`. Sibling dialogs that are remounted by a key take keys that differ from each other (`form-1`, `revoke-1`, `codes-1`).

`PickerField` is the choice of a degree level and of a programme, wherever one is chosen (DESIGN §12, "Picker"): a combobox in place from 768 px, a sheet with a search field below. It takes `options` in the order to offer them and never reorders; `any` is the first option of a filter. `SelectField`, the native select, is for the short fixed lists of the admin area.

A tab of the shell (`components/shell/tabs.ts`) may carry a `short` label for the tab bar of a phone, where a fifth of a 320 px screen is 64 px wide: "Leaderboard" reads "Ranking" there. `Notices` (`features/session/notices.tsx`, mounted by `AppGate`) tells what `/me` lists under `notices`, each as a toast, and marks it seen. The Barista's scanner remembers what it sent (`features/barista/scan-memory.ts`): a QR that stays in front of the camera is one request.

`Button` decides its own width: as wide as its column below 768 px, as wide as its label from there, also inside a column that stretches its children. `width="full"` (the buttons of a sheet, a pair that shares a row) and `width="fit"` (a button beside text, in a banner or a table) say otherwise. An icon button takes no width.

`Tabs` shows the focus ring for the keyboard only: a tab that was clicked or tapped has none, although the primitive focuses it with a script. The view of a tab is a keyboard stop of its own and keeps its ring.

The classes of a field (`fieldClasses`, `components/ui/input.tsx`) hold the look of every kind of field, and nothing else does: a refused field that has the focus keeps its border in `--no`.

## Reads and writes

A read: `useQuery(marketQuery(id))`, `useInfiniteQuery(ledgerQuery)`. The options carry the key, the request and the type of the answer. A list that pages sends no page size and asks for the next page with `next_cursor` (`LoadMore`).

A write a tap sends: `useSubmit(redeemCodeMutation)` (`lib/api/submit.ts`). It is `useMutation` with a `mutate` that guards itself: while a request of that write is out it sends nothing and returns false, so a second submit is stopped in the step that would send it (DESIGN §14). What the last render knew (`isPending`) is what the button shows, never the guard. Each mutation of `lib/api/mutations.ts` brings what the write does to the cache, so a screen never invalidates by hand. Add what is the screen's own (a toast, closing a sheet) in the callbacks of `mutate(variables, { onSuccess, onError })`, and do not replace the callbacks of the options. Disable the button while `isPending`: after a write without an answer the mutation stays pending until what it would have changed has been read again (DESIGN §14).

The two writes that carry an Idempotency-Key, the trade and the adjustment of a user's tokens, are kept submissions (`lib/api/submissions.ts`): each describes itself in `SubmissionRules`, gets its store with `submissionStore(client, rules)` and shows what the store holds through `useSubmissions`. Neither keeps a body or a key of its own.

`src/test/design-rules.test.ts` fails on `useMutation(` in any file under `app/`, `components/` or `features/`. The one exception is `features/session/notices.tsx`: marking a notice seen is the one write no tap sends, and each notice is a request of its own.

Every request goes out when it is made, whatever the browser says about the connection (`createQueryClient`). A read that cannot get through fails and the screen shows "No connection. Try again."; it is asked again by itself when the connection is back. A write that cannot get through fails at once and is never sent later by itself. No screen sets `networkMode` or `retry`.

| After | In the cache | After no answer |
| --- | --- | --- |
| Save profile | `me` is the answer; leaderboard | Nothing: saving again is safe |
| Mark a notice seen | The notice leaves `notices` of `me`; nothing is read again | Nothing is sent again; a later page load tells the notice once more |
| Trade | Balance and market from the answer; the market at once, its history, everything under `me`, the market lists, leaderboard | Nothing: the ticket sends the same request again |
| Redeem a code, get a voucher | Balance from the answer; everything under `me`, products, leaderboard | `me` and the ledger; the vouchers and `me` |
| Fresh voucher code | Nothing; the vouchers when the voucher is used up or gone | Nothing: asking again is safe |
| Redeem a voucher at the bar | Nothing | Nothing can be read: the barista scans again |
| Propose | The market is the answer, and the first of the caller's proposals; proposals, the market lists | Proposals |
| Admin action on a market | The market is the answer; its history, the market lists, risk, everything under `me`, leaderboard | The market, the market lists, risk |
| Create or revoke codes, acknowledge an alert | Batches, risk; the codes of a revoked batch are forgotten | Batches, risk |
| Suspend, reinstate, change a role | The user's row is the answer, in every list of users; the lists of users, and after the first two the leaderboard | The lists of users |
| Adjust a user's tokens | The user's balance from the answer, in every list of users; risk, leaderboard | Nothing: the dialog sends the same request again |

"View codes" reads the codes of a batch once per opening and never again behind the admin's back: the API records every read.

What changes the cache without a write of the screen is the event stream (below), and two reads: the trade ticket reads `/me` together with the market when it opens, as the Wallet does, because monthly tokens and refunds are booked between visits.

A write the contract gains gets its mutation and its row in that file; `lib/api/contract.test.ts` fails until it has one. `NOT_CALLED` of that test names the operations the app does not call through the client, the event stream among them.

An error is placed with the helpers of `lib/api/errors.ts`, never by digging in `details`: `placeError(error, fields)` for a form, `staleQuote`, `stakeLeft`, `retryAfterSeconds` and `useRetryAfter` for the cases DESIGN §14 names, `keepsSubmission(error)` for an answer that says nothing about a kept write.

## Trades

A buy made on screen is a number of shares: the text of the shares field, sent as it stands and judged by the quote the server gives for it. A sell is one of the two shortcuts of the holding, sent with the `shares` string that arrived. `QuoteRequest` (`lib/api/keys.ts`) is `{ side, outcome, shares }` on both sides, and `SharesEntry` draws the slider and the shortcuts of the selected outcome from its entry of `viewer.buy`. Nothing here reads the ends of the slider, the stake that is left or the balance as a limit.

`newSubmission(marketId, request)` serialises the body once and gives it one Idempotency-Key; `tradeMutation` sends exactly those. The guard is the quote itself: `max_cost_cr` of a buy and `min_proceeds_cr` of a sell are the quote's `amount_cr`.

Both are kept until an answer says what became of the request: a 2xx, or a 4xx other than 429. Then they are dropped and the next Confirm builds another body with another key; `QUOTE_STALE` is such a 4xx, so show `details.quote` and wait for the user. Every other outcome says nothing about the trade (a 5xx, a 429, the portal's 401, no response: `keepsSubmission(error)`): the button reads "Try again" and sends the stored body with the stored key again, unchanged. While they are kept the sheet offers nothing else, and closing and reopening it shows the same unanswered trade. They live in memory, not in browser storage.

After a 2xx the ticket shows the receipt as a banner over a form that is ready (`TradeReceiptBanner`); the next Confirm builds another body with another key and takes the banner away. Nothing reads "Done", and nothing closes the sheet but the user. `tradeMutation` marks the market out of date at once, so the slider, the shortcuts and the sell chips are those after the trade. A verdict that arrives while no ticket of the market is on screen is a toast.

A quote never outlives its input (`features/trade/use-quote.ts`): it counts only while it is the latest answer for what the ticket holds, it is dropped when the input is emptied, and it is asked for again after a refusal that says the screen is behind. No event and no poll touches it.

The ticket's state is in `features/trade/ticket-store.ts`: one state per market and query client, in memory, with `read`, `watch`, `choose`, `confirm` and `retry`. It is the only caller of `tradeMutation`. A host places `TradeTicket` straight into `ResponsiveSheet` or onto a panel surface: it brings its own `SheetBody` and `SheetFooter`. The host gives the sheet the question as `header`, learns from `useTradeOut(marketId, shown)` that a trade is on its way and sets `dismissible` by it, and writes the ticket's side and outcome into the address (`features/market-detail/trade-surface.ts`), which also holds the sheet against the phone's Back while a trade is out. `features/market-detail/market-detail-with-ticket.test.tsx` runs the real ticket inside the real screen; the other tests of the two features each put a stand-in in place of the other.

## Live data

The server pushes over one event stream, and `@/lib/live` is the app's side of it. Nothing on screen says whether it is open (DESIGN §14).

| File | Holds |
| --- | --- |
| `lib/live/client.ts` | `LiveClient`, a class without React: the `EventSource`, the topics that are wanted, the watchdog and the polling. One per page load; every stream it opens names the same `tab`. The only file that names `/api/stream` |
| `lib/live/provider.tsx` | `LiveProvider`, mounted by `AppGate` around everything that has a profile; `useLive`, `useLiveTopic`, `useLiveEvent`. Outside a provider the three hooks do nothing and `live` is false |
| `lib/live/rules.ts` | What an event does to the query cache: the table of DESIGN §14, the spread of `live_announce_spread_ms` and the viewer's read of `live_viewer_refresh_seconds`. It calls nothing but `invalidateQueries` and `setQueryData`, through the keys of `lib/api/keys.ts` |
| `lib/live/topics.ts`, `events.ts` | `liveTopic` (`markets()`, `market(id)`, `leaderboard()`), `LIVE_EVENT_NAMES`, `LiveEventName`, `LiveEventOf` |

- A screen says which topic it shows, `useLiveTopic(liveTopic.market(marketId))`, and does nothing else: the client follows the union of what the mounted screens name, and what the cache holds follows the events. A screen never works a figure out from an event.
- `useLiveEvent("market.status", handler)` is for what has to happen in front of the user (the bean drop of a payout), not for data.
- A price an event carries is written into the cache as it arrived, where its `version` is higher; everything else is marked out of date and read again. Whenever a stream opens (`ready`) the tab reads once what its topics cover, everything under `me` and the products: no event is sent a second time, so that read closes every gap.
- `useLive().live` is true from `ready` until a loss. While it is true `/me` is not read on navigation (`RouteWatch` of `components/shell/app-shell.tsx`); while it is false it is read on every navigation.
- After a loss the client polls every `live_poll_fallback_seconds` and tries the stream again at each poll, with no error on screen. A poll is a normal API call, so an ended portal session shows up there and reloads the page. A hidden page neither holds a stream nor polls.
- A figure that changes by itself is wrapped in `Flash`: the Price, the figures of the Leaderboard, the balance pill.
- An open quote of the trade ticket is never touched by an event or a poll.

One thing no event says: that a market reached its `close_at`. `useAskAtClose(closeTimes, ask)` sets one timer for the earliest of the times it is given and asks again every `live_poll_fallback_seconds` while the answer still shows the same state. Market detail and the Markets list time the markets they show as open for trading; Market detail, My proposals and the admin's Markets screen time the proposals they show in review, which expire at that moment. The timer asks and decides nothing.

Live are the Markets list, Market detail, the Leaderboard, the balance and the notices. A screen reads everything else when it opens and when the window gets the focus back.

## Tests

- Beside the code: `thing.test.tsx`. `renderWithProviders` (`@/test/render`) gives a query client with the tunables and a user in it; `mockApi` (`@/test/http`) answers `fetch` by contract route and records the requests; fixtures typed by the contract are in `@/test/fixtures` (`me`, `profileSaved`, `notice`, `config`, `marketDetail`, `marketViewer`, `leaderboard` and the rest).
- A request without a route fails the test. `json`, `apiError`, `portal401`, `errorPage`, `noAnswer` and `deferred` make the answers. A test of a `FORBIDDEN` answer needs a route for `GET /api/me`, because the client asks who is calling.
- The router is in memory (`@/test/navigation`): `navigation.go("/leaderboard?sort=trades")` before rendering, `navigation.href` after. A screen that keeps state in its query string writes it with the browser's History API; `followHistoryWrites()` in `beforeEach` makes the router follow those writes and returns the two spies. The Wallet's tests of `?code=` look at `window.location` instead, and set the address in both places.
- A test starts as a phone with reduced motion: `setViewport("wide")` and `setMediaQuery` (`@/test/media`) change that. `intersect()` scrolls a list to its end, and `watchScrolling()` (`@/test/observers`) says where an element lies. `sizeCharts()` (`@/test/charts`) gives a Recharts chart a size, without which jsdom draws none.
- No test opens a stream or polls unless it asks to: `renderWithProviders(ui, { live: true })` mounts `LiveProvider`, and without it the live hooks are inert. `installFakeEventSource` (`@/test/fake-event-source`) plays the events of a stream.
- After every test the set-up puts the toasts away and waits one animation frame for sonner to tell its subscribers (`clearToasts()` of `@/test/toasts`): sonner keeps its toasts outside React. A test that stands for two page loads calls `await clearToasts()` between them.
- With a clock, use `vi.useFakeTimers({ shouldAdvanceTime: true })` so that `user` and `findBy` keep working, and `vi.useRealTimers()` in a `finally`.
- A test whose screen compares a fixture's time with now fixes the day: `vi.useFakeTimers({ now: TODAY, toFake: ["Date"] })` with `TODAY` of `@/test/fixtures`, which lies before every close time of the fixtures, and `vi.useRealTimers()` afterwards. The tests of Market detail, the Markets list, My proposals, Propose and the admin's Markets do.
- The cache tells its observers a moment after it changed: after an event or a `setQueryData`, wait with `findBy` or `waitFor` before looking at the screen.
- Assert on what the user sees and on what was sent. An expected amount is the string of the fixture, never arithmetic in the test.
- A feature's helpers for its own tests live in the feature: `features/leaderboard/test-players.ts` (`player()`, `you()`), `features/market-detail/testing.tsx` (the stand-in for the trade feature). The admin's tests spy on `browser.print` and `browser.saveFile` (`features/admin/browser.ts`) and mock the QR component.
- `src/test/design-rules.test.ts` fails on a forbidden class, colour, word or call in any file under `src/`.

**The browser suite.** `npm run e2e` runs the specs of `e2e/` (`*.spec.ts`) in the Chrome that is installed, each at the projects "phone" (375 × 667) and "desktop" (1024 × 768), one after the other. It starts no stack: it drives the one at `E2E_BASE_URL` (`http://localhost:8080` when left out), which has to be up and seeded. `E2E_IDENTITY=cookies` (when left out) names a person with the cookies `dev_user_id` and `dev_user_name` of the development proxy; `E2E_IDENTITY=headers` sends `X-User-Id` and `X-User-Name` itself, for a stack of `compose.yaml` alone. `e2e/support/runner.ts` lists every variable the runner reads. vitest sees `src/**/*.test.{ts,tsx}` and nothing of `e2e/`.

- A spec imports from `./support` only: `test` with the fixture `people`, `STUDENTS`, `expect`, `expectLayoutHolds` and `screenshot`. `people.as(STUDENTS.anna)` is a browser context of a student of the seed; `people.join(degree?)` makes a person up (`e2e.<run>.<n>@ethz.ch`) who sets up their own profile.
- A spec reads the seeded people and markets and changes only what it made itself, so it passes again on the same database.
- A spec ends with `expectLayoutHolds(page)` and `screenshot(page, testInfo, screen)` for each of its screens: nothing lies over anything else, the page does not scroll sideways, and the picture lies in the directory `E2E_SCREENSHOTS` names (`test-results/screens` when left out) as `<spec>.<screen>.<project>.png`. What the layout check measures and leaves out stands at the top of `e2e/support/layout.ts`.
- A spec that counts requests for images (`e2e/images.spec.ts`) routes and intercepts no image request: that would switch the browser's cache off.

## Recipes

**Build a screen.** (1) Read its section in `DESIGN.md` §13 and its rows in §14. (2) Work in `src/features/<name>/`; keep the names and props its `index.ts` exports. (3) Start from `<Screen>`, read with the options of `lib/api/queries.ts`, write with those of `lib/api/mutations.ts` through `useSubmit`. (4) Tests for its four states and for what it sends.

**Use an endpoint the contract gained or changed.** (1) `make api-client`. (2) A key in `lib/api/keys.ts` and a `queryOptions` in `lib/api/queries.ts` for a read; a `mutationOptions` with its cache handling in `lib/api/mutations.ts` for a write. (3) Types come from `@/lib/api/schema`; never retype a response. (4) A fixture in `src/test/fixtures.ts`.

**Show a value that follows the stream.** (1) Its row in the table of `DESIGN.md` §14, "Live data". (2) The screen names its topic with `useLiveTopic`. (3) What the event does to the cache goes into `lib/live/rules.ts`, as a write of what the event carries or as a read. (4) A figure that changes by itself gets `Flash`. (5) A test with `{ live: true }` and the fake event source.

**Add a shadcn component.** (1) Name it in `DESIGN.md` §12. (2) `npx shadcn@4.21.4 add <name>`. (3) `git diff src/app/globals.css` must be empty. (4) Restyle it: tokens, 48 px controls, the `type-*` utilities, no `dark:`, no shadow.

**Change a colour, font or radius.** `DESIGN.md` §9 and `src/app/globals.css` in the same commit; a test compares the two.

**Put a pixel illustration in.** Add the file to `public/brand/pixel/` and set its slot's `art` in `src/components/brand/illustration.tsx`.

**Handle an error code.** It arrives in the generated `ErrorCode`. Add its row to `DESIGN.md` §14 and treat it where it occurs; read `details` through a helper of `lib/api/errors.ts`.

**Print.** Lay the sheet out before the tap, as a child of `body` that brings its own `@page` and `@media print` rules, and call `browser.print()` in the tap handler itself with nothing rendered or awaited in between (`features/admin/print-sheet.tsx`). A print that starts later than the tap is one a browser may refuse.

## Versions that are pinned on purpose

ESLint 9 (the plugins of `eslint-config-next` do not accept 10), TypeScript 5.9 (`openapi-typescript` needs 5), jsdom 29 and `@testing-library/jest-dom` 6.9.1 (later ones need Node 22), Recharts 3.8.0 (what the `chart` component was written for). `redocly.yaml` holds the rules `openapi-typescript` validates `backend/openapi.json` with, each with its reason. `next.config.ts` sets `agentRules: false`, so that `next dev` writes no rules file of its own beside this one. No combobox package is installed: the picker is written on `radix-ui`, `vaul` and `lucide-react`, which are.
