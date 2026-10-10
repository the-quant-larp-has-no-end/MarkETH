# MarkETH design system

The visual and interaction rules for the MarkETH frontend, and the specification of its screens. SPEC §11 says which screens exist and SPEC A.11 fixes the direction: Polymarket's layout, a dark navy theme, the MarkETH logo. This file is the full system those two sections point to.

## 1. How to use this file

- Build a screen from §13, using only the components in §12 and the tokens in §9.
- The tokens in §9 are the contents of `frontend/src/app/globals.css`. A colour, font or radius changes in both places in the same commit.
- Components never contain a raw hex value, pixel font size or ad-hoc radius. They use the Tailwind classes the tokens generate (`bg-card`, `text-yes-text`, `rounded-lg`, `type-h3`).
- Field names and paths are those of `backend/openapi.json`. Paths are written without their `/api` prefix: `/me` is `GET /api/me`. A screen shows no field that file does not have.
- Decisions this file takes that the spec left open are marked **Decision**. Changing one means editing this file first.
- Where this file and `docs/SPEC.md` disagree on behaviour, the spec wins and this file is wrong.

### Direction

**Polymarket, with playful, gamified ETH aspects.** Every design decision is checked against that sentence.

- *Polymarket* gives the structure: a dense, dark, flat trading product where the list of markets is the home page and numbers do the talking.
- *Playful and gamified* gives the character: pixel art, the coffee-bean token, trophies, a leaderboard, beans that drop when you are paid.
- *ETH* gives the setting: campus places and student life in the copy and illustrations, tokens that end up as a coffee or a snack at the bar.

The test for any new element has two questions. Would this be at home on Polymarket? If not, is it a pixel-art or campus touch from §3? An element that is neither does not belong.

### What MarkETH must not look like

The default output of a UI kit or an AI tool is a generic startup or SaaS site. MarkETH is a trading game, not a product to be marketed, and none of the following is ever built.

| Never | Do this instead |
| --- | --- |
| **Gradients of any kind**: on backgrounds, buttons, text, borders, cards, charts, skeletons or images | Flat fills from §4. The chart's area fill is one flat colour at 12% opacity |
| A landing page: hero headline with subheading and call-to-action, "how it works" in three steps, feature grid of icon cards, testimonials, logo wall, FAQ, pricing, newsletter, big footer | The first screen is the Markets list, as on Polymarket. The one thing that can stand in front of it, once, is the profile set-up: a logo, three fields and one button |
| Marketing copy: "Unlock", "Seamless", "Supercharge", "Powerful", "Reimagined", "The future of", exclamation marks | Plain statements and dry campus humour (§3) |
| Glass and glow: translucent blurred panels, `backdrop-filter`, neon outlines, glowing buttons, coloured or soft drop shadows, floating cards | Opaque surfaces separated by tone and 1 px hairlines (§6) |
| The SaaS palette: purple, indigo, violet, teal-to-blue, "brand gradient" accents, pure white or pure black | The five colours of §4 and their derived tints, nothing else |
| Decorative blobs, mesh backgrounds, grid or dot patterns, noise textures, abstract 3D shapes, isometric scenes | A plain `--background`. Decoration is pixel art from §3 or nothing |
| Stock illustration styles (flat vector people, line-art spot illustrations), photographs, emoji used as icons | Pixel art for illustration, lucide for interface icons |
| Icons in tinted rounded squares or circles heading every card | An icon only where it is a control or a status |
| Dashboard chrome: a left sidebar, a row of KPI tiles above user content, widgets, greeting headers ("Welcome back, Anna") | Top bar, content, tab bar. Stat tiles exist only on the admin risk page |
| Everything a pill: fully rounded buttons and inputs, oversized radii, large soft cards with lots of empty padding | The radii of §6. Dense cards, 16 px padding |
| Centred, airy marketing layout: huge headings, wide margins, one idea per screen height | Left-aligned, compact, several markets visible without scrolling |
| Generic delight: confetti, count-up number animations, parallax, scroll-triggered reveals, shimmer sweeps, bouncing call-to-actions | The three bean and Polybahn animations of §7, nothing else |
| Onboarding carousels, product tours, coach marks, cookie-style popups, upsell banners, "Pro" badges | One welcome dialog after the profile is set up |
| The untouched shadcn/ui look: zinc or slate greys, default radius, default shadows, Inter for headings | shadcn/ui components restyled to these tokens; Barlow for headings and numbers |

If a generated screen shows any item in the left column, it is wrong even if it looks polished.

### Reference: Polymarket

Polymarket is the model for layout and interaction. When this file is silent, do what Polymarket's mobile site does, in MarkETH's colours.

| Taken from Polymarket | Where |
| --- | --- |
| Dark, flat surfaces separated by tone and hairlines, not shadows | §4, §6 |
| Market cards: question, a large "% chance" figure, soft-tinted "Yes" and "No" buttons, volume in the footer | §12 |
| Market page: question, large chance figure, line chart, then the trade ticket, then rules | §13 |
| Trade ticket: Buy / Sell tabs, outcome buttons that carry their price, quick-amount chips, one wide action button | §13 |
| The Markets list as the home page, with no landing page in front of it | §11, §13 |
| Logo with the name at the top left of every main screen | §3, §11 |
| Bottom tab bar on phones; a ticket panel beside the chart on desktop | §6, §11 |
| White percentage figures; green and red kept for the Yes and No controls | §4 |

| Deliberately different | Why |
| --- | --- |
| No "to win" or profit figure, no cents | The API sends none and the frontend may not compute one (golden rule 6) |
| No deposit, cash-out or payment screens | Nothing is for sale (golden rule 9) |
| No sign-in, sign-up or sign-out | The portal in front of the app signs every visitor in before a page loads (§11) |
| No comments, categories, search or market images | Not in the API (SPEC A.1) |
| A leaderboard with pixel trophies for the top three | Polymarket's leaderboard, made into a podium (§13) |
| A coffee-bean token in place of the dollar sign | The unit is a token; enough of them turn into a coffee at the bar |
| Pixel-art campus touches and ETH in-jokes (§3) | Polymarket is neutral; MarkETH belongs to one campus |
| Stake presets only, no free amount when buying | SPEC §4 stake limit; presets come from the server |

## 2. Principles

1. **Phone first.** Every screen is designed at 375 px wide and one thumb. Wider layouts are the phone layout with more room, never a different product.
2. **Numbers are the content.** The probability is the largest thing on any screen that has one. Chrome stays quiet so numbers can be loud.
3. **The server owns every number.** The frontend formats what the API returns and never computes a price, balance, limit, profit, rank, order or eligibility (root `CLAUDE.md`, golden rule 6). If a screen needs a number the API does not send, the screen does without it.
4. **Calm navy, scarce colour.** The canvas is navy, the accent is the off-white `#FAFAFF`. Green and red appear only where they mean YES, NO, gain or loss.
5. **One primary action per screen.** There is at most one white button visible at a time.
6. **Serious numbers, playful edges.** The trade ticket, balances and errors are sober. Personality lives around them: the bean, the pixel art, a campus joke in an empty state (§3).
7. **Nothing is for sale.** No screen, button or sentence suggests buying tokens, codes or vouchers (golden rule 9, SPEC §2). Tokens are given, a code is redeemed, and tokens are turned into a voucher: the wallet says "get", never "buy", "price" or "order". Those words belong to shares alone, where the trade sheet uses them ("Buy YES", "Average price", "The price moved."). Avoid "top up", "deposit", "cash out" and any currency symbol everywhere.

## 3. Brand

### Name

- In text the product is written **MarkETH**: capital M, capital ETH, one word.
- Inside the logo the name is set as "markETH", with a lowercase m. That spelling exists only in the logo image; it is never typed.
- The ETH Zurich logo is never used (SPEC A.11). A VIS logo may appear only in the footer of the profile set-up screen, and only with VIS's agreement.

### Logo

There are two forms of the logo, both supplied by the team.

| Form | What it is | Where it is used |
| --- | --- | --- |
| **Logo with name** | The cup with the rising arrow, followed by "markETH" | The top left of the top bar, as on Polymarket; the profile set-up screen and the full-screen message (§13); the printable QR sheet |
| **Logo** | The cup with the rising arrow alone | Everything that identifies the app in a square: browser tab, home-screen icon, splash, and anywhere the name does not fit |

The originals are kept, untouched, in `frontend/design/`. The files the app serves are derived from them and live in `frontend/public/brand/`. Images must be served by the app itself: the Content-Security-Policy allows no remote image.

| File in `frontend/public/brand/` | What it is | Use |
| --- | --- | --- |
| `logo-lockup.png` | Logo with name in `#FAFAFF`, transparent, 716 x 168 px | Top bar (28 px tall), profile set-up and the full-screen message (48 px tall) |
| `logo-lockup-dark.png` | Logo with name in `--ink`, transparent | The printable QR sheet only |
| `logo-mark.png` | Logo in `#FAFAFF`, transparent, 320 x 320 px | Where only a square fits, for example a narrow header beside a back arrow |
| `logo-mark-dark.png` | Logo in `--ink`, transparent | The printable QR sheet only |
| `icon-512.png`, `icon-192.png` | `#FAFAFF` logo on `#1E2749`, square | Web manifest (home-screen icon) |
| `apple-touch-icon.png` | The same, 180 x 180 px | iOS home-screen icon |
| `favicon.png` | The same, 48 x 48 px | Browser tab |
| `token.png` | The token, transparent, 256 x 256 px | The token icon (below) |
| `trophy-1.png`, `trophy-2.png`, `trophy-3.png` | Gold, silver and bronze pixel trophies, transparent, 256 x 256 px | First, second and third place on the leaderboard (below) |

- The originals are black, which is invisible on navy. The app uses the same shapes in `#FAFAFF`; nothing but the colour is changed.
- The name in the logo is part of the image. It is not rebuilt from a font, and no text is placed beside the logo with name. The image carries `alt="MarkETH"`.
- The logo sits only on `--background` or `--card`. Clear space on every side is at least half the height of the cup. Never stretch, rotate, outline, animate or add effects, and never tint it green or red.
- The minimum height of the logo with name is 24 px. Below that, use the logo alone.
- A new logo replaces the originals in `frontend/design/`, and the served files are regenerated from them at the sizes above.

### Token

The unit of the app is the **token**. Tokens are what a trade is made with and what turns into a voucher for a coffee or a snack; how many tokens a product takes comes from the API. Its symbol is a pixel-art coffee bean.

- The **token icon** is the bean centred on a `#FAFAFF` disc, the bean filling 70% of the disc. The bean's dark outline disappears on navy, so the bean never sits on a navy surface without its disc.
- Sizes: 16 px beside `number` and `body-sm` figures, 20 px beside `number-lg`, 32 px beside `display`, 64 px in the welcome dialog. The gap to the figure is a quarter of the icon's size.
- The icon comes **before** the figure: (bean) 25. In tables in this file, "(bean)" stands for the token icon.
- The icon is decorative (`alt=""`); the accessible text of an amount is always the number and the word "tokens" (§15).
- The bean's brown is not a colour of the interface. No text, border or surface uses it.

### Trophies

Three pixel-art trophies mark the top three places of the leaderboard: gold for first, silver for second, bronze for third.

- The supplied originals are JPEGs on a white background. The served files are the same drawings with the background removed, including the openings of the handles. Never use the JPEGs in the app: a white box on navy is exactly what the cut-outs avoid.
- A trophy sits directly on `--background` or `--card`, with no disc, tile, ring or shadow behind it.
- Sizes: 96 px for first place and 72 px for second and third on the podium; 24 px beside a username in a leaderboard row. Never larger than 96 px and never smaller than 24 px.
- A trophy always comes with the rank as text ("1st", "2nd", "3rd"). Its `alt` text is "First place", "Second place" or "Third place".
- Trophies mean a rank on the leaderboard and nothing else. They are not used as generic "success" or "win" icons, on market cards, or for a resolved market.
- Gold, silver and bronze exist only inside these images, like the bean's brown. They are not colours of the interface: no gold text, no silver border, no bronze badge.
- Ranks from fourth down have no trophy and no medal, only the number.

### Campus touches

Polymarket's structure, with the humour of one campus. These elements make MarkETH feel like it belongs to ETH without using ETH's logo or typeface.

**Pixel art.** The token is pixel art, so pixel art is the illustration style of the whole product. Spot illustrations are drawn on a 16 x 16 or 24 x 24 grid, shown at whole multiples of that grid with `image-rendering: pixelated`, and coloured only with `#FAFAFF`, `--muted-foreground`, `--raised` and the bean's browns. The token and the trophies are the only pixel art with colours of their own. They live in `frontend/public/brand/pixel/`.

| Illustration | Where |
| --- | --- |
| Empty coffee cup | Empty portfolio, empty wallet activity |
| Polybahn car | Page-level loading (below), "not found" screen |
| Main building dome | Profile set-up footer, empty Markets list |
| Steaming cup | Voucher issued |
| Lecture-hall blackboard | Propose market: the empty list of "My proposals" |
| Alarm clock | Closed market waiting for its result |

These files are not drawn yet. Until one exists, its place is taken by the matching 32 px lucide icon; no screen waits for an illustration.

**Trophies and the leaderboard.** The game layer: a podium for the top three, and your own rank in view wherever you stand (§13).

**Beans in motion.** The bean is the one thing that may move for fun (§7): beans drop into the balance when tokens arrive, and a single bean bounces on a finished trade.

**The Polybahn loader.** A route change that takes longer than 300 ms shows a 2 px `#FAFAFF` progress line under the top bar with the pixel Polybahn car riding its front edge.

**Campus copy.** Empty states, the profile set-up, the welcome dialog, the "not found" screen and a few results carry one dry campus reference. These are the lines and where they stand:

| Place | Copy |
| --- | --- |
| Empty Markets list | "No markets yet. Quieter than the HG on a Sunday." |
| Empty portfolio | "No positions yet. Your cup is empty." |
| Not found | "This page does not exist. Not even in the HG basement." |
| Profile set-up | "Pick a name for the leaderboard. The podium remembers." |
| Welcome dialog | "You got {balance_cr} tokens. Spend them wisely, or on coffee." |
| Voucher issued | "Show this at the bar. You earned it, sort of." |
| Barista accepted | "Paid in predictions." |
| Closed, awaiting result | "Trading is over. Now we wait, like for exam results." |
| Leaderboard, nobody with a figure | "Nobody here has a resolved market yet. The podium is yours to take." |
| Rate limited | The API's message, unchanged |

Rules that keep it from getting tiresome:

- At most one campus line per screen, with its illustration where it has one, and none inside the trade sheet, a form in progress, or the admin area. The token, the trophies and the bean animations of §7 are the product's own look and are not counted.
- Errors, balances, limits and anything about why a trade or code failed are never joked about. They show the API's message.
- References are to places and shared experiences (HG, CAB, Polyterrasse, Polybahn, Mensa, ASVZ, exam session). Never to named people, departments' reputations, grades of anyone, politics or religion (SPEC §6 content rules apply to our copy too).
- Jokes are written once and reviewed like any other copy. They are static strings, not generated.
- Nothing playful blocks a tap, delays a response or replaces a label.

### Voice

- English only. Short, plain, second person: "You have 240 tokens.", "The price moved."
- Error text comes from the API's `message` field and is shown as sent (§14).
- The unit is called **tokens** everywhere in the interface. The words "credits" and "CR" do not appear on screen, and "voucher" is never the name of the unit. Figures carry the token icon; sentences say "token" or "tokens" (§10).
- One word, one meaning. A **code** is what VIS hands out and a user redeems for tokens ("Redeem code"). A **voucher** is what tokens turn into: the QR a user shows at the bar. The characters under that QR are the "voucher code", never "code" alone. A voucher is for one **product**, called by the name the API sends ("Coffee", "Mate"). Tokens that arrive by rule are "Welcome tokens" and "Monthly tokens".
- Say "market", "shares", "YES" and "NO" (capitals). Do not say "bet", "odds", "gamble" or "win money".
- Buttons are verbs: "Confirm", "Redeem code", "Get a voucher". Sentence case everywhere, no full stop on buttons or headings.

## 4. Colour

Dark theme only. There is no light theme and no theme switch.

### Palette

| Name | Hex | Role |
| --- | --- | --- |
| Navy 900 | `#1E2749` | Page background |
| Navy 700 | `#273469` | Surfaces: cards, sheets, bars |
| White | `#FAFAFF` | Accent: text, primary buttons, focus |
| Green | `#06A77D` | YES, gains |
| Red | `#EF3E36` | NO, losses, errors, destructive actions |

Everything else is derived from these five by mixing `#FAFAFF`, or a colour, into navy.

**"White" in this file always means `#FAFAFF`.** Pure white (`#FFFFFF`) and pure black (`#000000`) are never used on screen, including in QR codes, images, overlays and third-party component defaults.

### Tokens

| Token | Hex | Use |
| --- | --- | --- |
| `--background` | `#1E2749` | Page canvas; fill of inputs |
| `--card` | `#273469` | Cards, bottom sheet, top bar, tab bar |
| `--raised` | `#384475` | Pressed and hover state, selected chip, skeleton |
| `--scrim` | `#14192F` at 70% | Overlay behind sheets and dialogs |
| `--foreground` | `#FAFAFF` | Primary text, icons, primary button fill, focus ring |
| `--muted-foreground` | `#B0B5CA` | Secondary text, labels, placeholders, inactive tab |
| `--subtle` | `#7B83A5` | Disabled text and icons only |
| `--border` | `#495481` | Hairlines and card outlines (decorative) |
| `--border-strong` | `#7B83A5` | Outlines that mark a control: inputs, secondary buttons |
| `--ink` | `#0B0F24` | Text on green and red fills |
| `--yes` | `#06A77D` | YES button fill, chart line, large YES figures |
| `--yes-text` | `#68C8B1` | Green text below 24 px |
| `--yes-soft` | `#22466C` | Tinted background for a YES selection or result |
| `--no` | `#EF3E36` | NO button fill, destructive button fill, large NO figures |
| `--no-text` | `#F5A5A5` | Red text below 24 px, error text |
| `--no-soft` | `#473661` | Tinted background for a NO selection, result or error banner |

### Contrast

Measured WCAG 2 contrast ratios. Small text needs 4.5, large text (24 px and up, or 19 px bold) and graphics need 3.

| Foreground | on `--background` | on `--card` | on `--raised` |
| --- | --- | --- | --- |
| `--foreground` | 14.0 | 11.3 | 9.0 |
| `--muted-foreground` | 7.2 | 5.8 | 4.6 |
| `--subtle` | 3.9 | 3.2 | 2.5 |
| `--yes` | 4.7 | 3.8 | 3.0 |
| `--yes-text` | 7.3 | 5.9 | 4.7 |
| `--no` | 3.8 | 3.0 | 2.4 |
| `--no-text` | 7.5 | 6.1 | 4.8 |
| `--border-strong` | 3.9 | 3.2 | 2.5 |

| Label | on `--foreground` fill | on `--yes` fill | on `--no` fill |
| --- | --- | --- | --- |
| `--background` navy | 14.0 | 4.7 | 3.8 |
| `--ink` | 18.2 | 6.2 | 4.9 |
| `--foreground` (`#FAFAFF`) | n/a | 3.0 | 3.7 |

What follows from the numbers:

- The pure green and red are for fills, the chart line and figures of 24 px or more. Smaller coloured text uses `--yes-text` and `--no-text`.
- Labels on green and red fills use `--ink`, never white.
- Pure `--no` red is never placed on `--raised`.
- `--subtle` is only for disabled content, which is exempt from contrast rules. It is never used for text a user needs to read.

### Usage rules

- **White is the accent.** The primary button is a white fill with a navy label. The active tab, the focus ring and the selected segment are white.
- **Green and red are reserved** for exactly these: the YES and NO buttons and toggle, a YES or NO probability figure, the chart line, the sign of a ledger or payout amount, a resolved outcome, error text and destructive buttons.
- Green and red are **never** used for navigation, links, generic buttons, borders of neutral cards, icons without an outcome meaning, illustrations or the status badges of OPEN, CLOSED, HALTED and VOID markets.
- **Colour never carries meaning alone.** A YES figure carries the label "YES", an amount carries its sign, an error carries an icon and text.
- Full-strength `--yes` and `--no` fills are rare: the outcome buttons on Market detail, the selected toggle segment, the Confirm button and the barista result. Lists use the soft tints (`--yes-soft`, `--no-soft`) with the text tints, as on the market card.
- No gradients, no glows, no coloured shadows, anywhere (§1, "What MarkETH must not look like").

## 5. Typography

| Family | Use | Weights |
| --- | --- | --- |
| Barlow | Headings, every number, button labels | 500, 600, 700 |
| Inter | Body text, form input, labels, captions | 400, 500, 600 |

Both are loaded with `next/font/google`, which downloads them at build time and serves them from the app's own origin. The Content-Security-Policy (`font-src 'self' data:`) blocks any font CDN. ETH's DIN Next is licensed and is not used (SPEC A.11).

| Style | Font | Size / line height | Weight | Use |
| --- | --- | --- | --- | --- |
| `display` | Barlow | 48 / 52 px | 600 | The probability on Market detail |
| `h1` | Barlow | 28 / 34 px | 600 | Screen title |
| `h2` | Barlow | 22 / 28 px | 600 | Section title, market question on detail |
| `h3` | Barlow | 18 / 24 px | 600 | Card title, market question on a card |
| `number-lg` | Barlow | 24 / 28 px | 600 | Probability on a card, balance, quote figures |
| `number` | Barlow | 16 / 24 px | 500 | Amounts in rows and tables |
| `label` | Barlow | 16 / 20 px | 600 | Button labels |
| `body` | Inter | 16 / 24 px | 400 | Paragraphs, form input |
| `body-sm` | Inter | 14 / 20 px | 400 | Secondary text, row subtitles |
| `caption` | Inter | 12 / 16 px | 500 | Field labels, badges, timestamps |

- Every number uses Barlow with `font-variant-numeric: tabular-nums`, so live prices do not shift the layout.
- Form inputs are 16 px. Anything smaller makes iOS zoom the page on focus.
- Text is left aligned. Numbers in a column are right aligned.
- No all-caps text except "YES", "NO" and status badges. No italics.
- Market questions wrap to at most three lines on a card and are never truncated on the detail screen.

## 6. Layout and spacing

### Spacing

A 4 px grid, using Tailwind's default scale: 4, 8, 12, 16, 24, 32, 48 px.

| Where | Value |
| --- | --- |
| Screen side gutter | 16 px |
| Padding inside a card or sheet | 16 px |
| Gap between cards in a list | 12 px |
| Gap between sections | 24 px |
| Gap between a label and its field | 8 px |

### Shape

| Token | Value | Use |
| --- | --- | --- |
| `--radius-sm` | 6 px | Badges, small chips |
| `--radius-md` | 10 px | Buttons, inputs, preset chips |
| `--radius-lg` | 14 px | Cards |
| `--radius-xl` | 20 px | Top corners of the bottom sheet, dialogs |
| `rounded-full` | | Avatars, the segmented control, the sheet handle |

- Depth comes from surface colour, not shadow: `--background`, then `--card`, then `--raised`. Cards have a 1 px `--border` outline. The only shadow in the system is under the bottom sheet and dialogs.
- Dividers inside a card are 1 px `--border`.

### Breakpoints

| Width | Layout |
| --- | --- |
| Below 768 px | One column, bottom tab bar, trade ticket as a bottom sheet |
| 768 px and up | Content column up to 720 px, centred. Markets list in two columns. Tabs move into the top bar. The trade ticket is a centred dialog, 420 px wide |
| 1024 px and up | Content up to 1080 px. Markets list in three columns. Market detail shows the trade ticket as a sticky right-hand panel, 360 px wide |

Admin screens are the one exception: their tables are designed for 1024 px and scroll horizontally below that.

### Touch

- Every tap target is at least 44 x 44 px (SPEC §11). Buttons and inputs are 48 px tall; the trade Confirm button is 56 px.
- Targets are at least 8 px apart.
- Fixed bars respect the safe area: the tab bar adds `env(safe-area-inset-bottom)`, the top bar `env(safe-area-inset-top)`. The viewport meta sets `viewport-fit=cover` and `theme-color` `#1E2749`.
- Nothing depends on hover. Hover styles exist for pointers and mirror the pressed style.

## 7. Motion

| Token | Value | Use |
| --- | --- | --- |
| `--duration-fast` | 120 ms | Press feedback, colour changes |
| `--duration-base` | 200 ms | Toasts, badges, tab changes |
| `--duration-sheet` | 280 ms | Bottom sheet and dialog |
| `--ease-out` | `cubic-bezier(0.2, 0.8, 0.2, 1)` | Everything entering |
| `--ease-in` | `cubic-bezier(0.4, 0, 1, 1)` | Everything leaving |

- **Price change:** when a live price changes, the figure's background flashes `--raised` for 600 ms and fades. The flash is neutral, not green or red, so a busy market does not strobe colour.
- **Chart:** a new point extends the line over 200 ms. The chart never re-animates from zero on an update.
- **Sheet:** slides up from the bottom while the scrim fades in; it can be dragged down to dismiss.
- **Loading:** skeleton blocks in `--raised` with a slow opacity pulse (1.2 s). No spinners except inside a button that is waiting for its response.
- **Bean drop:** when tokens arrive in front of the user, three to five token icons fall into the figure that grows over 600 ms, then the figure updates. Three arrivals count: the welcome tokens (into the amount of the welcome dialog), a redeemed code (into the balance card of the Wallet) and a payout that comes while its market is on screen (into "Paid out", §13). It plays once per arrival and never on page load: tokens booked while nobody was looking (monthly tokens, a refund, the payout of a market that was not on screen) simply show in the balance. It also plays once for a viewer who opens the leaderboard in first place (§13).
- **Bean bounce:** the trade receipt's token icon bounces once (200 ms).
- **Polybahn loader:** the progress line and its car (§3). It never blocks input.
- With `prefers-reduced-motion: reduce`, movement is removed: the bean drop, bean bounce and Polybahn car are not shown, the sheet and toasts cross-fade, the flash and the pulse are disabled.

## 8. Icons and imagery

- Icons are `lucide-react`, the set shadcn/ui ships with. Stroke 2 px; 20 px inline, 24 px in bars.
- An icon alone is never a control's only label, except the back arrow, the close cross and the tab bar, which all carry an `aria-label` (the tab bar also shows text).
- Icons are `--foreground` or `--muted-foreground`. An icon is green or red only inside an element that already is.
- Markets have no images and there is no photography. Illustration is pixel art only, from the short list in §3; empty states use one illustration (or a 32 px icon until it is drawn) and a sentence.
- The only raster images are the logo, the token icon, the trophies, the pixel illustrations (§3) and generated QR codes.

## 9. Tokens

The stack is Tailwind CSS v4 with shadcn/ui components copied into `frontend/src/components/ui/`. shadcn/ui reads the standard variable names below; the MarkETH extras follow them. This block is the token section of `frontend/src/app/globals.css`.

```css
@import "tailwindcss";
@import "tw-animate-css";

:root {
  color-scheme: dark;

  /* shadcn/ui roles */
  --background: #1e2749;
  --foreground: #fafaff;
  --card: #273469;
  --card-foreground: #fafaff;
  --popover: #273469;
  --popover-foreground: #fafaff;
  --primary: #fafaff;
  --primary-foreground: #1e2749;
  --secondary: #384475;
  --secondary-foreground: #fafaff;
  --muted: #384475;
  --muted-foreground: #b0b5ca;
  --accent: #384475;
  --accent-foreground: #fafaff;
  --destructive: #ef3e36;
  --destructive-foreground: #0b0f24;
  --border: #495481;
  --input: #7b83a5;
  --ring: #fafaff;
  --radius: 0.625rem;

  /* MarkETH extras */
  --raised: #384475;
  --scrim: rgb(20 25 47 / 0.7);
  --subtle: #7b83a5;
  --border-strong: #7b83a5;
  --ink: #0b0f24;
  --yes: #06a77d;
  --yes-text: #68c8b1;
  --yes-soft: #22466c;
  --no: #ef3e36;
  --no-text: #f5a5a5;
  --no-soft: #473661;
  --chart-1: #06a77d;

  --duration-fast: 120ms;
  --duration-base: 200ms;
  --duration-sheet: 280ms;
  --ease-out: cubic-bezier(0.2, 0.8, 0.2, 1);
  --ease-in: cubic-bezier(0.4, 0, 1, 1);
}

@theme inline {
  --color-background: var(--background);
  --color-foreground: var(--foreground);
  --color-card: var(--card);
  --color-card-foreground: var(--card-foreground);
  --color-popover: var(--popover);
  --color-popover-foreground: var(--popover-foreground);
  --color-primary: var(--primary);
  --color-primary-foreground: var(--primary-foreground);
  --color-secondary: var(--secondary);
  --color-secondary-foreground: var(--secondary-foreground);
  --color-muted: var(--muted);
  --color-muted-foreground: var(--muted-foreground);
  --color-accent: var(--accent);
  --color-accent-foreground: var(--accent-foreground);
  --color-destructive: var(--destructive);
  --color-destructive-foreground: var(--destructive-foreground);
  --color-border: var(--border);
  --color-input: var(--input);
  --color-ring: var(--ring);
  --color-raised: var(--raised);
  --color-scrim: var(--scrim);
  --color-subtle: var(--subtle);
  --color-border-strong: var(--border-strong);
  --color-ink: var(--ink);
  --color-yes: var(--yes);
  --color-yes-text: var(--yes-text);
  --color-yes-soft: var(--yes-soft);
  --color-no: var(--no);
  --color-no-text: var(--no-text);
  --color-no-soft: var(--no-soft);
  --color-chart-1: var(--chart-1);

  --radius-sm: 0.375rem;
  --radius-md: 0.625rem;
  --radius-lg: 0.875rem;
  --radius-xl: 1.25rem;

  /* set on <html> by next/font in layout.tsx */
  --font-sans: var(--font-inter), system-ui, sans-serif;
  --font-display: var(--font-barlow), system-ui, sans-serif;
}

@utility type-display   { font-family: var(--font-display); font-size: 3rem;     line-height: 3.25rem;  font-weight: 600; font-variant-numeric: tabular-nums; }
@utility type-h1        { font-family: var(--font-display); font-size: 1.75rem;  line-height: 2.125rem; font-weight: 600; }
@utility type-h2        { font-family: var(--font-display); font-size: 1.375rem; line-height: 1.75rem;  font-weight: 600; }
@utility type-h3        { font-family: var(--font-display); font-size: 1.125rem; line-height: 1.5rem;   font-weight: 600; }
@utility type-number-lg { font-family: var(--font-display); font-size: 1.5rem;   line-height: 1.75rem;  font-weight: 600; font-variant-numeric: tabular-nums; }
@utility type-number    { font-family: var(--font-display); font-size: 1rem;     line-height: 1.5rem;   font-weight: 500; font-variant-numeric: tabular-nums; }
@utility type-label     { font-family: var(--font-display); font-size: 1rem;     line-height: 1.25rem;  font-weight: 600; }
@utility type-body      { font-family: var(--font-sans);    font-size: 1rem;     line-height: 1.5rem;   font-weight: 400; }
@utility type-body-sm   { font-family: var(--font-sans);    font-size: 0.875rem; line-height: 1.25rem;  font-weight: 400; }
@utility type-caption   { font-family: var(--font-sans);    font-size: 0.75rem;  line-height: 1rem;     font-weight: 500; }
```

- In shadcn/ui's vocabulary `--accent` is the hover surface, so it maps to `--raised`. The brand accent, white, is `--primary`.
- There is no `.dark` class and no `prefers-color-scheme` query: the dark values are the only values.
- `font-display` is the Tailwind class for Barlow; `font-sans` (the default) is Inter.
- The ten styles of §5 are the utilities `type-display` to `type-caption`: family, size, line height and weight in one class. The prefix is `type-`, not `text-`, so `tailwind-merge` never takes one for a colour. `tw-animate-css` supplies the enter and leave classes of dialogs.

## 10. Showing data

The API sends display-ready values. The frontend may format them and must not derive new ones.

| Value | Arrives as | Shown as |
| --- | --- | --- |
| Probability (`p_yes`, `p_no`, `p_yes_after`) | Number in [0, 1], already rounded | Percentage with `price_display_decimals` decimals from `/config`: `54.8%` |
| Amount (`*_cr`) | Decimal string, trailing zeros trimmed | The token icon, then the string: (bean) `25`, (bean) `1,250.5` |
| Shares | Decimal string | The string, then "shares": `183.29 shares` |
| Average price (`average_price`) | Number in [0, 1], already rounded | (bean) `0.524` per share |
| Signed amount (ledger `amount_cr`) | Signed decimal string | Sign, token icon, figure: `+` (bean) `200` in `--yes-text`, `−` (bean) `25` in `--foreground` |
| Date and time | ISO 8601 | In the `/config` `timezone`, as `14 Oct 2026, 18:00` |
| Day (`next_monthly_grant_on`) | ISO 8601 date | The day as sent, without conversion: `1 Nov 2026` |
| Deadline (`close_at`) in a list | ISO 8601 | Relative when under 7 days ("Closes in 3 days", "Closes today, 18:00"), otherwise the date. When `trading_open` is false, the date alone |
| Code | `code_length` characters (`/config`) | Groups of four, `XXXX-XXXX-XXXX`, Barlow, letter-spacing 0.08em |
| Voucher code | `voucher_code_length` characters (`/config`) | `XXXX-XXXX`, same style |
| Product (`name`, `price_cr`) | String and decimal string | The name as sent, then the token icon and the string: Coffee (bean) `35` |
| Rate (`win_rate`) | Number in [0, 1], already rounded, or null | A percentage like a probability: `62.5%`. Null is an en dash |
| Ratio (`avg_payout_ratio`) | Number, already cut to two decimals, or null | Two decimals and a multiplication sign: `1.35×`. Null is an en dash |
| Count (`trades`, `rank`) | Integer | Digits grouped in threes: `1,204` |
| Degree (`degree_level`, `degree_programme`) | A level and the label of a programme | The programme as sent: "BSc Computer Science". A level is shown by its `name` from `/config` `degrees` |

Rules:

- **Amounts are strings.** Never pass a `*_cr` or `shares` value through `parseFloat` or `Number`. Where a list needs a shorter figure, cut the string after the second decimal place (cutting, not rounding, so the app never shows more than the user has). The full string is shown on detail views: the trade receipt and the expanded ledger row. **Decision:** from 1,000 up, the whole part is grouped in threes with a comma, by inserting the commas into the string.
- **Zero is the one thing read from an amount.** The API trims zeros, so an amount of zero always arrives as the single character `0`. A screen may test for that string to leave out what would be empty (a holding of no shares, a market the viewer never bought into, a payout of nothing). **Decision:** this is a test of the string, not arithmetic. A screen never orders two amounts, adds them or measures one against a limit: `allowed`, `affordable`, `can_trade`, `can_get_voucher` and `can_settle` are the server's answers to those questions.
- **Percentages** are formatted with `Intl.NumberFormat("en-GB", { style: "percent" })` and the `/config` decimals. `p_no` comes from the API; the frontend does not subtract. An average price is formatted the same way without the percent style, with two decimals more.
- **Times** are shown and entered in the `/config` `timezone`, whatever the device is set to. A time the user enters is sent as ISO 8601 with its offset.
- **Tokens out are not red.** A trade or a voucher lowering the balance is normal, so negative ledger amounts are white. Red is for losses and errors.
- **No derived figures.** There is no profit, loss, return or "potential winnings" figure, because the API sends none. What a position is worth, took and gave back are the API's `value_cr`, `cost_cr`, `proceeds_cr` and `net_stake_cr`, and after settlement `payout_cr` or `refund_cr`; a screen shows the ones §13 names, each labelled. The win rate and the payout ratio of the leaderboard are the server's figures too.
- **No hard-coded tunables.** The stake limit, the presets, the welcome and monthly tokens, what a product takes, the days a voucher is valid, the lengths of a username, a code and a text field, and the list of degrees come from `/config` or from the response that shows them. Copy is written with placeholders: "of {stake_limit_cr} tokens", never "of 100 tokens".
- **Icon or word.** A standalone figure (balance, row amount, quote value, chip) gets the token icon and no word. A figure inside a sentence gets the word and no icon: "You got 240 tokens." The word follows the string: "token" when the string is `1`, otherwise "tokens".
- **Names in code are unchanged.** The API, the database and the spec call the unit a credit (`*_cr`, "CR"). "Token" is its name on screen only; do not rename fields.
- **Keys are not copy.** `status`, `tier`, `degree_level`, `sort`, `side` and `severity` arrive as keys. The words for them are in this file (the badges of §12, the names in §13) or in `/config` (`degrees`). Only "YES" and "NO" are shown as they arrive.
- **Server text.** API messages, ledger labels and product names are shown as sent. They are written in this file's vocabulary ("Not enough tokens for this.", "Welcome tokens", "Voucher: Coffee"); wording is corrected in the backend, never on screen.
- **People.** A player is shown by `username` and degree. A real name or an email address is shown in two places only: the viewer's own, on the profile set-up and Profile screens, and `proposer_email` in the admin area. No other screen, row, toast or message shows either.
- Locale is fixed to `en-GB` for numbers and dates. The minus sign is U+2212, the en dash of a missing figure U+2013 and the multiplication sign of a ratio U+00D7.

## 11. App shell and navigation

Nobody is signed out. The portal in front of the app signs every visitor in through ETH before a page loads, so the app has no sign-in screen, no sign-out button and no signed-out state. What the app asks of a first visitor is a profile (§13).

### Top bar

56 px tall, `--card`, sticky. Left: the logo with name (`logo-lockup.png`, 28 px tall, 16 px from the edge), linking to Markets. This is its fixed place on every main screen (Markets, Portfolio, Leaderboard, Wallet, Admin, Barista), as on Polymarket; from 768 px the tabs follow it on the same line. Right: the balance as a pill (token icon and `balance_cr` of `/me`, Barlow 600) linking to Wallet, then a profile button (the lucide `user-round` icon, 44 px target, `aria-label` "Profile") linking to `/profile`. On inner screens the left side is a back arrow and the screen title; the right side stays. The back arrow goes one step back in the history; on a screen that was opened directly it goes to Markets, and from the admin view of a market to Admin.

### Tab bar

64 px tall plus the bottom safe area, `--card`, 1 px `--border` on top, fixed. Each tab is an icon above a `caption` label. The active tab is `--foreground`; the others are `--muted-foreground`. There is no coloured indicator.

| Tab | Route | Shown to |
| --- | --- | --- |
| Markets | `/` | Everyone |
| Portfolio | `/portfolio` | Everyone |
| Leaderboard | `/leaderboard` | Everyone |
| Wallet | `/wallet` | Everyone |
| Scan | `/barista` | Role BARISTA |
| Admin | `/admin` | Role ADMIN |

The role comes from `MeResponse.role`. Admins reach the scanner from the Admin screen. There are never more than five tabs. The tab bar is hidden on the screens that stand in front of the app (start-up, profile set-up, the full-screen message), on the Barista scanner while the camera is live, and while the trade sheet is open.

### Routes

| Route | Screen | Top bar | Fixed by |
| --- | --- | --- | --- |
| `/` | Markets | Main | this file |
| `/markets/[id]` | Market detail; the Trade sheet opens over it | Inner, no title | this file |
| `/portfolio` | Portfolio | Main | this file |
| `/wallet` | Wallet; `?code=...` opens the redeem sheet with the code filled in | Main | SPEC A.6 |
| `/leaderboard` | Leaderboard; sort and filters live in the query string | Main | this file |
| `/propose` | Propose market | Inner, "Propose a market" | this file |
| `/profile` | Profile: username and degree | Inner, "Profile" | this file |
| `/admin` | Admin: proposal queue and markets | Main | this file |
| `/admin/markets/[id]` | Admin: one market and its actions | Inner, no title | this file |
| `/admin/codes` | Admin: code batches | Main | this file |
| `/admin/risk` | Admin: risk panel | Main | SPEC §12 |
| `/barista` | Barista scanner | Main | this file |
| every route | Start-up, and in place of the route Profile set-up or the full-screen message | None | SPEC A.3 |
| any other path | Not found | Main | this file |

- A visitor without a profile sees the profile set-up screen in place of whatever route they opened and continues to that route once it is saved. The address is kept meanwhile, so someone who scanned a code card arrives in the Wallet with the code.
- A user without the role sees the "not found" screen, not a permission lecture.
- Three routes read their query string. `/wallet` reads `?code=...` (§13, Wallet). `/markets/[id]` reads `?buy=YES`, `?buy=NO`, `?sell=YES` and `?sell=NO`, which open the trade sheet on that side and outcome. `/leaderboard` keeps its sort and filters there.

## 12. Components

Each component lives in `frontend/src/components/` and builds on the shadcn/ui primitive named. All share these states: **default**, **pressed** (`--raised` or 90% opacity on fills), **focus-visible** (2 px `--ring` outline, 2 px offset), **disabled** (`--subtle` text, 50% fill, no pointer events) and, where it applies, **loading**.

### Button (shadcn `button`)

| Variant | Look | Use |
| --- | --- | --- |
| `primary` | `--primary` fill, `--primary-foreground` label | The one main action of a screen |
| `secondary` | Transparent, 1 px `--border-strong`, white label | Any other action |
| `ghost` | No outline, white label | Low-emphasis actions, "Cancel" |
| `destructive` | `--no` fill, `--ink` label | Reject, void, revoke. Always behind a confirmation dialog |
| `yes` / `no` | `--yes` or `--no` fill, `--ink` label | Outcome buttons, and the Confirm button of a buy |

Height 48 px (56 px for the trade Confirm), `--radius-md`, `label` text style, full width on phones. A loading button keeps its width, shows a spinner in place of its label and is disabled.

### Outcome buttons and toggle

- **Outcome buttons** (Market detail): two equal buttons side by side, `yes` on the left and `no` on the right. Each shows the outcome and its price: "YES 54.8%". They open the trade sheet with that outcome selected.
- **Outcome toggle** (trade sheet, shadcn `toggle-group`): two segments. The selected one is filled (`--yes` or `--no`, `--ink` label); the other is `--background` with a `--muted-foreground` label. Each segment has `aria-pressed`.
- The outcome buttons, the selected toggle segment and the Confirm button of a buy are the only full green and red fills in the trading flow. Everywhere else, Yes and No use the soft tints.

### Price

The probability figure. Sizes: `display` on Market detail, `number-lg` on cards. It shows the YES percentage in `--foreground` white with a `caption` `--muted-foreground` "chance" label, beside the figure on Market detail and under it on a card, as Polymarket does ("54.8% chance"). It turns green or red only for a resolved market (§ Status badge). It flashes on change (§7).

**Decision:** live probabilities are white, not green, as on Polymarket. Colour belongs to the Yes and No controls.

### Market card (shadcn `card`)

`--card`, `--radius-lg`, 1 px `--border`, 16 px padding, the whole card is one link. Top to bottom:

1. A row: the question (`h3`, up to three lines) on the left, the Price (`number-lg`, with "chance" under it) on the right.
2. Two soft outcome buttons side by side, 44 px tall: "Yes" (`--yes-soft` fill, `--yes-text` label) and "No" (`--no-soft` fill, `--no-text` label). Each is a link to the market with the trade sheet open on that outcome (`/markets/[id]?buy=YES`, `/markets/[id]?buy=NO`). They are shown only when `trading_open` is true.
3. A footer row in `body-sm` `--muted-foreground`: the token icon and `volume_cr` with "Vol." on the left, the deadline (§10) on the right.
4. A status badge in place of the buttons when the market is not open for trading.

The card itself links to the market; the two buttons are separate links inside it. This is Polymarket's card. The buttons use the soft tints, never the full `--yes` and `--no` fills, so a long list stays calm.

### Status badge (shadcn `badge`)

`caption` text, uppercase, `--radius-sm`, 4 x 8 px padding.

| `status` | Badge | Style |
| --- | --- | --- |
| OPEN | none on cards; "Open" on detail | `--raised` fill, white text |
| CLOSED | "Closed" | `--raised` fill, `--muted-foreground` text |
| HALTED | "Paused", with a pause icon | 1 px `--border-strong` outline, white text |
| RESOLVED, outcome YES | "Resolved YES" | `--yes-soft` fill, `--yes-text` text |
| RESOLVED, outcome NO | "Resolved NO" | `--no-soft` fill, `--no-text` text |
| VOID | "Voided" | `--raised` fill, `--muted-foreground` text |
| PROPOSED | "In review" | `--raised` fill, white text |
| REJECTED | "Not approved" | `--raised` fill, `--muted-foreground` text |

The badge shows the `status` the API sends, which is already the effective status (an OPEN market past its close time arrives as CLOSED). Whether trading is possible is read from `trading_open`, never from the clock.

### Probability chart

Plots `p_yes` from `GET /markets/{market_id}/history` over time.

- A **step line** (each trade holds its price until the next), 2 px, `--yes`, with a `--yes` area fill at 12% opacity beneath it.
- The y axis is fixed from 0% to 100% with gridlines at 25, 50 and 75% in `--border`; labels are `caption`, `--muted-foreground`. The x axis shows the first and last date only.
- The series always starts at the 50% opening point the API includes, so a new market shows a flat line, not an empty box.
- Height 200 px on phones, 280 px from 768 px. Touching the chart shows a vertical guide and a tooltip with the percentage and time.
- A new stream event appends a point. An event whose `version` is not newer than the last drawn one is ignored.
- Built on Recharts through shadcn/ui's `chart`. Accessible alternative in §15.

### Stake presets

A row of equal chips from `viewer.stake_presets`, each showing the token icon and `amount_cr`. Height 48 px, `--radius-md`. Default: 1 px `--border-strong`. Selected: `--foreground` fill, `--primary-foreground` text. A preset with `allowed: false` is disabled. The frontend never decides this itself (SPEC A.11).

Under the row, in `body-sm` `--muted-foreground`: "{stake_left_cr} of {stake_limit_cr} tokens left in this market".

### Quote panel

A `--background` inset block inside the sheet, `--radius-md`, with label and value rows:

| Label | Value |
| --- | --- |
| You pay (buying) / You receive (selling) | (bean) `amount_cr` |
| Shares | `shares` |
| Average price | (bean) `average_price` per share |
| New probability | `p_yes_after` YES |

Labels are `body-sm` `--muted-foreground`; values are `number`, right aligned. While a quote is loading, the values are skeleton bars and the labels stay put, so the sheet does not jump. Below the panel, one line: "Each share pays 1 token if {outcome} wins."

### Bottom sheet (shadcn `drawer`)

`--card`, top corners `--radius-xl`, a 36 x 4 px `--border-strong` handle, `--scrim` behind. Maximum height 90% of the viewport; content scrolls inside while the action button stays pinned at the bottom above the safe area. Closes by drag, by tapping the scrim, or with the close button. From 768 px it is a `dialog`. The welcome dialog is that `dialog` at every width.

### Filter chips

A row of single-choice chips that scrolls horizontally: the status filter of Markets, the sort of the Leaderboard. Each chip is 44 px tall with 12 px side padding, `--radius-sm` and `body-sm` text. Default: 1 px `--border-strong`, `--muted-foreground` label. Selected: `--raised` fill, `--foreground` label, `aria-pressed="true"`. A chip that states a filter which is set carries a close cross and removes that filter when tapped.

### Tabs and the segmented control (shadcn `tabs`, `toggle-group`)

- **Tabs** switch between the views of one screen (Portfolio: Positions and History; Admin: Markets, Codes, Risk). A row 48 px tall with `label` text. The active tab is `--foreground` with a 2 px `--foreground` line under it; the others are `--muted-foreground`.
- **The segmented control** switches one form between two modes (Buy and Sell). 48 px tall, `rounded-full`, a `--background` track. The selected segment is a `--foreground` fill with a `--primary-foreground` label; the other has a `--muted-foreground` label.

### Form fields (shadcn `input`, `textarea`, `label`, `native-select`)

- Label above the field (`caption`, `--muted-foreground`). Field 48 px tall, `--background` fill, 1 px `--border-strong`, `--radius-md`, 16 px text. Placeholder in `--muted-foreground`.
- Focus: the border becomes `--ring` and 2 px.
- Error: the border becomes `--no`, with the message below in `body-sm` `--no-text` next to an alert icon, linked by `aria-describedby`.
- Help text and character limits sit below the field in `caption`.
- The code fields use the code style (§10), `autocapitalize="characters"`, `autocorrect="off"`, and insert the dashes as the user types.
- A choice from a fixed list (degree level, programme, a status) is a native `select` styled like a field (shadcn `native-select`): the phone's own picker is faster than a custom one. A select that depends on another is disabled until the other is set.

### List rows

56 px minimum height, 1 px `--border` divider, no outline of their own.

| Row | Left | Right |
| --- | --- | --- |
| Ledger | `description`, and `created_at` below in `caption` | Signed `amount_cr` |
| Trade | `market_question` (one line), and "Bought YES" or "Sold NO" with the date below | `amount_cr`, and `shares` below |
| Position | The question, and holdings below ("183.29 YES shares") | `value_cr`, with a status badge when settled |
| Product | `name` | The token icon and `price_cr`; the whole row is disabled when `affordable` is false |
| Voucher | `product_name`, and the expiry below in `caption` | "Show QR", or the status when it is used up |
| Leaderboard | `rank`, then `username` with `degree_programme` below in `caption` | The figure of the measure the list is sorted by |

A ledger row expands on tap to show the full `amount_cr` string. A position row and a trade row are each one link to their market. A list that pages has `next_cursor` in its answer: when the user reaches its end, the next page is asked for with that value as `cursor`.

### QR code and scanner

- **QR code:** always `--ink` modules on a `#FAFAFF` tile with 16 px padding and `--radius-lg`, at least 240 px wide. A dark-themed QR does not scan reliably. The code is repeated as text below it. Showing a voucher raises the screen brightness hint ("Turn up your brightness") instead of trying to control it.
- **Scanner:** a full-width camera view with a square guide in `--foreground` corners. It reports what it read once and pauses until the screen asks for the next scan. It always has a visible typed alternative ("Type the code instead"; on the Barista screen "Type the voucher code"), and a clear message when camera permission is refused. The camera needs HTTPS (SPEC A.10). Whatever decodes the picture is served by the app itself: the Content-Security-Policy allows no remote script, wasm file or worker.

### Feedback

| Component | Based on | Use |
| --- | --- | --- |
| Toast | shadcn `sonner` | Confirmation of a finished action ("Profile saved", "+{value_cr} tokens"). Bottom of the screen, above the tab bar, 4 s. `--raised` fill, white text |
| Banner | shadcn `alert` | A condition that lasts: a paused market, a 503. Full width inside the content column, with an icon. Neutral: `--raised`. Error: `--no-soft` with `--no-text` |
| Inline error | | Under the field or above the action button it concerns |
| Confirmation dialog | shadcn `alert-dialog` | Confirmation of destructive or irreversible admin actions only |
| Skeleton | shadcn `skeleton` | Loading. Shapes match the content they replace |
| Empty state | | One pixel illustration (§3), one sentence, and at most one button |

Errors are never shown only as a toast: a toast disappears, and the user must be able to read what went wrong.

## 13. Screens

Each screen lists its content from top to bottom. Field names are those of the API responses. Every visitor is signed in, and everyone past the profile set-up has a profile, so no screen has a signed-out state.

| Screen | Reads | Writes |
| --- | --- | --- |
| Start-up | `GET /config`, `GET /me` | |
| Profile set-up, Profile | | `PUT /me/profile` |
| Markets | `GET /markets` | |
| Market detail | `GET /markets/{market_id}`, `GET /markets/{market_id}/history`, `GET /markets/{market_id}/stream` | |
| Trade sheet | `GET /markets/{market_id}/quote` | `POST /markets/{market_id}/trades` |
| Portfolio | `GET /me/positions`, `GET /me/trades` | |
| Wallet | `GET /me`, `GET /me/vouchers`, `GET /me/ledger`, `GET /vouchers/products` | `POST /vouchers`, `POST /vouchers/{voucher_id}/code`, `POST /codes/redeem` |
| Leaderboard | `GET /leaderboard` | |
| Propose market | `GET /me/proposals` | `POST /markets` |
| Admin | `GET /markets`, `GET /markets/{market_id}`, `GET /admin/code-batches`, `GET /admin/risk` | The seven market actions listed there, `POST /admin/code-batches`, `POST /admin/code-batches/{batch_id}/revoke`, `POST /admin/alerts/{alert_id}/ack` |
| Barista | | `POST /barista/vouchers/redeem` |

No screen calls `GET /health` or `POST /admin/grants/monthly`.

### Start-up (every route)

What stands in front of the app until it knows who is looking. The portal has signed the visitor in before the page loads; start-up finds out whether that person has a profile.

1. The boot screen: the top bar with the logo and nothing on its right, skeleton blocks in the content column, no tab bar.
2. The app asks `/config` and `/me` together. The answer of `/me` decides what takes the place of the boot screen:

   | `/me` answers | What is shown |
   | --- | --- |
   | The profile | The route that was opened, inside the app shell |
   | `PROFILE_REQUIRED` | Profile set-up, in place of the route |
   | `FORBIDDEN` | The full-screen message with the API's `message`: the address is one MarkETH is not open to, or the account is suspended |
   | `UNAUTHENTICATED` | The full-screen message with the API's `message` and "Try again": the request reached the API without the portal's identity |
   | The portal's 401 (§14) | The page reloads once. If the reload brings the same answer: the full-screen message "Your ETH sign-in has ended." and "Try again" |
   | No answer, or an error of the server | The full-screen message with the text §14 gives that failure, and "Try again" |

   A `/config` that fails counts as the last row.

**The full-screen message** is plain on purpose: no top bar, no tab bar, the logo with name (48 px tall) centred, and under it one message in `body`. It is the API's `message` as sent; only the portal's 401 and a missing answer have a text of ours, because nothing was sent. "Try again", where the table names it, is a secondary button that reloads the page. There is no illustration and no joke.

### Profile set-up (every route)

Shown in place of the route while `/me` answers `PROFILE_REQUIRED`: the first visit. No top bar, no tab bar. Content centred vertically. The address stays as it was opened.

1. The logo with name, 48 px tall and centred, then one sentence: "Trade on campus questions with free tokens. Enough of them get you a coffee at the bar."
2. A line in `body-sm` `--muted-foreground`: "Signed in as {name}", the name the portal sent (`details.name` of the answer). When that is empty, the line names `details.email` instead.
3. Three fields:
   - **Username** (`username`). Help text: "{username_min_length} to {username_max_length} characters: lower-case letters, digits and underscores.", both lengths from `/config`. `autocapitalize="none"`, `autocorrect="off"`, `spellcheck="false"`. A capital letter is turned into its lower-case letter as it is typed, so the field shows what is sent, and the field takes no more than `username_max_length` characters.
   - **Level** (`degree_level`): a select of the levels in `/config` `degrees`, each shown by its `name` and sent as its `level`.
   - **Programme** (`degree_programme`): a select of the `programmes` of the chosen level, disabled until a level is chosen. Changing the level clears it.
4. One sentence in `body-sm` `--muted-foreground`: "Other players can see your username, your degree, your tokens and your trading record."
5. A primary "Create profile" button, disabled until all three fields are filled. It sends `PUT /me/profile`.
6. A footer, below the form: the main-building dome and under it, in `body-sm` `--muted-foreground`, "Pick a name for the leaderboard. The podium remembers."

The answer is the profile, and the route that was opened takes the place of this screen. When the answer says `signup_granted: true`, the **welcome dialog** opens over that route: the 64 px token icon, "You got {balance_cr} tokens" (`h2`), "Spend them wisely, or on coffee. A share pays 1 token if you are right and nothing if you are wrong.", and a primary "Start trading" that closes it. The amount is `balance_cr` of the same answer: a profile created a moment ago holds its welcome tokens and nothing else. The bean drop plays once. A sheet the route would open by itself (the redeem sheet of a code card) waits until the dialog is closed.

States: `USERNAME_TAKEN` and a `VALIDATION_ERROR` (a username the API does not accept or keeps reserved, a programme that is not on the list) appear on the field they name, with the API's message; the other fields keep their input. `FORBIDDEN` replaces the screen with the full-screen message. No answer or a 503 shows the banner above the button and keeps the input; saving again is safe.

### Profile (`/profile`)

An inner screen, reached from the profile button in the top bar. It changes the same two things the set-up asks for: the username and the degree.

1. Top bar with a back arrow and the title "Profile".
2. A card: `username` (`h2`) and `degree_programme` in `body-sm` `--muted-foreground`; under a divider `name` and `email` with the caption "From your ETH sign-in. Only you and VIS see these." An empty `name` is left out.
3. The three fields of the set-up screen, filled in, the same sentence about what other players can see, and a primary "Save" button that is disabled until something changed.

States as on the set-up screen. Success shows the toast "Profile saved" and stays on the screen. There is no welcome dialog here and nothing to sign out of.

### Markets (`/`)

1. Top bar.
2. Filter chips (§12): "Open" (the default), "Closed", "Resolved", "All". Each sends its `status` to `GET /markets`:

   | Chip | `status` |
   | --- | --- |
   | "Open" | OPEN and HALTED, so a paused market stays in sight |
   | "Closed" | CLOSED |
   | "Resolved" | RESOLVED and VOID |
   | "All" | None: the API then lists every status but PROPOSED and REJECTED |

3. Market cards, in the order the API returns them.
4. A secondary "Propose a market" button after the list, leading to `/propose`.

States: six skeleton cards while loading. Empty: the dome illustration and "No markets yet. Quieter than the HG on a Sunday." with the propose button. Error: a banner with "Try again". The list is not live; it refetches on focus and on pull to refresh.

### Market detail (`/markets/[id]`)

The answer of `GET /markets/{market_id}` always carries its `viewer` block, because whoever reads a market has a profile. No part of this screen has a signed-out form.

1. Top bar with a back arrow and no title.
2. The question (`h2`) and the status badge.
3. The Price in `display` with its "chance" label, and under it `volume_cr` with "Vol." and the deadline in `body-sm`.
4. The probability chart.
5. The outcome buttons (YES left, NO right), shown while the market is OPEN or HALTED and enabled when `viewer.can_trade` is true. On phones they are pinned to the bottom of the screen above the tab bar once the user scrolls past them.
6. **Your position**, shown once the viewer has bought into the market (`viewer.cost_cr` is not `0`, §10):
   - a line for each entry of `viewer.holdings` whose `shares` is not `0`: "183.29 YES shares" and its `value_cr`. When there is none, the line "You hold no shares.";
   - "Your stake" with `net_stake_cr`;
   - while the outcome buttons are shown, the stake-left line of §12 and a secondary "Sell" button, enabled when at least one entry of `viewer.sell_presets` has `allowed: true`.
7. **Rules:** "YES means" (`yes_definition`), "NO means" (`no_definition`), "How it is decided" (`resolution_criteria`), "Source" (`resolution_source`), "Trading closes" (`close_at`) and "Decided by" (`resolve_by`).
8. **If the market is voided:** the `void_rule` text, shown in full and not collapsed. SPEC §6 requires it to be visible before anyone trades.
9. **Resolution** (when the market is RESOLVED or VOID): the outcome badge, and `evidence` and `resolved_at` of `resolution`.

States by status:

| Situation | What changes |
| --- | --- |
| OPEN, and `cannot_trade_reason` is `OWN_MARKET` | The outcome buttons are disabled. Neutral banner: "You proposed this market, so you cannot trade it." |
| HALTED | Neutral banner "Trading is paused." The outcome buttons and "Sell" are disabled; the chart and position stay |
| CLOSED | Neutral banner with the alarm clock: "Trading is over. Now we wait, like for exam results." |
| RESOLVED | The Price is replaced by the outcome ("YES" in `--yes`, or "NO" in `--no`, `display` size). In **Your position**, "Paid out" with `payout_cr` takes the place of "Your stake". When the market resolves while it is on screen (a `status` event brought the change) and `payout_cr` is not `0`, the bean drop plays into that figure (§7) |
| VOID | Neutral banner "This market was voided. Stakes were refunded." In **Your position**, "Refunded" with `refund_cr` takes the place of "Your stake" |
| PROPOSED, REJECTED | The API shows these to their proposer and to admins only. The badge, the rules and, when rejected, `rejection_reason`. No Price, chart, outcome buttons or position, and the screen asks for no history and no stream |

At most one banner shows. The market's status decides it, and `OWN_MARKET` speaks only while the market is OPEN. The other reason, `MARKET_NOT_OPEN`, has no words of its own: the row of the status has said it.

Live: the Price, chart and volume follow the stream (§14). A `status` event refetches the screen.

States: skeletons in the shape of the Price, the chart and the rules while loading. A market the API does not know is the "not found" screen. Error: a banner with "Try again".

### Trade sheet (`/markets/[id]`)

Opened over Market detail from an outcome button, from "Sell", or by the address (`?buy=YES`, `?buy=NO`, `?sell=YES`, `?sell=NO`). An address that asks for a trade the viewer cannot make (`viewer.can_trade` is false, or a sell with no preset that is allowed) opens the market without the sheet. Below 768 px it is the bottom sheet, from 768 px a dialog, and from 1024 px the sticky panel of §6, which is in place whenever the outcome buttons are shown and starts on Buy and YES. Top to bottom:

1. The question, one line, `body-sm` `--muted-foreground`.
2. A **Buy / Sell** segmented control. "Sell" is enabled when at least one entry of `viewer.sell_presets` has `allowed: true`.
3. The outcome toggle. When selling, an outcome is offered when one of its two entries in `viewer.sell_presets` has `allowed: true`.
4. **Buying:** the stake presets and the stake-left line. **Selling:** two chips, "Half" and "All", from the entries of `viewer.sell_presets` for the selected outcome (`portion` HALF and ALL). Each shows its `shares` and is disabled when `allowed` is false. The chosen preset's `shares` string is sent as it arrived.
5. The quote panel.
6. A line with the balance: "Balance" and the token icon with `viewer.balance_cr`.
7. The Confirm button, 56 px. Its fill follows the outcome when buying ("Buy YES" on `--yes`, "Buy NO" on `--no`) and is `primary` white when selling ("Sell YES shares").

Selling follows SPEC A.11: the server works out half and all of each holding and says whether each sale is allowed, so the frontend halves and compares nothing. There is no free-entry shares field.

Behaviour:

- The quote comes from `GET /markets/{market_id}/quote`, asked for `quote_debounce_ms` (from `/config`) after the last change, with `side`, `outcome` and the preset as `amount_cr` (buying) or as `shares` (selling). Confirm is disabled until a quote for the current input has arrived.
- Confirm sends `POST /markets/{market_id}/trades` with the preset as it arrived and the quote as its guard. Buying: `side`, `outcome`, `amount_cr`, `min_shares` (the quote's `shares`) and `quote_version`. Selling: `side`, `outcome`, `shares`, `min_proceeds_cr` (the quote's `amount_cr`) and `quote_version`. A price that moved against the user is therefore always answered `QUOTE_STALE`.
- On Confirm the button shows its spinner and is disabled until the response arrives. A double tap cannot create two trades. The request carries an `Idempotency-Key` that belongs to that one request body; §14 says how long the two are kept and when "Try again" sends them again.
- The panel shows the quote, not the chip: "You pay" is the quote's `amount_cr`.
- **Success:** the sheet content is replaced by a receipt: a check icon, "Bought 183.29 YES shares" or "Sold 183.29 YES shares", "You paid" or "You received" with `amount_cr`, "New probability" with `p_yes_after`, "Balance" with `balance_cr`, and "Done". The bean bounce plays (§7). The market screen behind it updates from the response.
- **`QUOTE_STALE`:** the panel updates to the quote in `details.quote`, changed values flash, and an inline notice above the button shows the API's `message` ("The price moved. Review the new quote and confirm again."). The button returns to enabled. Nothing is submitted automatically.
- **Other errors:** shown inline above the button with the API's `message`; the sheet stays open and the input is kept. `LIMIT_EXCEEDED` also refreshes the stake-left line from `stake_left_cr` and `stake_limit_cr` in `details`.

### Portfolio (`/portfolio`)

1. Top bar.
2. A summary card: "Value of open positions" with `total_value_cr` in `number-lg`.
3. Tabs: **Positions** and **History**.
4. Positions: position rows from `GET /me/positions` in API order, under the subheadings "Open" and "Settled"; a market is settled when its `status` is RESOLVED or VOID. A row names each holding whose `shares` is not `0` ("183.29 YES shares"), or "No shares held" when there is none. A settled row shows "Paid out" or "Refunded" with the token icon and `payout_cr` or `refund_cr` in place of `value_cr`. A row opens the market.
5. History: trade rows from `GET /me/trades`, loading more with `next_cursor` as the user scrolls. A row opens its market.

Empty: the empty-cup illustration and "No positions yet. Your cup is empty." with a primary "Browse markets" button. An empty History says "No trades yet." Loading: a skeleton summary card and five skeleton rows. Error: a banner with "Try again".

### Wallet (`/wallet`)

1. Top bar.
2. Balance card: the 32 px token icon and `balance_cr` in `display`, labelled "Your tokens", and below it "Next monthly tokens: {monthly_grant_cr} on {next_monthly_grant_on}".
3. Two buttons side by side: primary "Get a voucher", secondary "Redeem code".
4. **Vouchers** from `GET /me/vouchers`, as voucher rows (§12) in the API's order. A voucher whose `status` is ACTIVE shows "Valid until" with `expires_at` and a "Show QR" button. The others are muted and show "Redeemed" with `redeemed_at`, or "Expired, refunded". With none, one line: "No vouchers yet."
5. **Activity:** ledger rows from `GET /me/ledger`, paged with `next_cursor`.

Flows:

- **Redeem code** opens a sheet with the code field, a "Scan QR" button and a primary "Redeem", which sends the code alone to `POST /codes/redeem`. Success closes the sheet with the toast "+{value_cr} tokens", sets the balance to the answer's `balance_cr` and plays the bean drop. A failure shows the API's `message` under the field and nothing of ours. For a code that cannot be redeemed that is always the same sentence ("This code is not valid."), and the screen never hints at why (SPEC §2).
- **A code card** carries a link, `/wallet?code=...`. The Wallet reads `code` from the address when it opens, removes it from the address at once (replacing the history entry, so that no address, back step or bookmark keeps it) and opens the redeem sheet with the field filled in. The user still taps "Redeem". "Scan QR" inside the sheet reads the same cards: it takes the `code` out of a link, or takes what it read as it is when that is no link, and fills the field. A link without a `code` fills nothing, and the sheet says "No code in this QR." A link is never sent to the API.
- **Get a voucher** opens a sheet with two steps.
  1. The products from `GET /vouchers/products`, in the API's order, as product rows (§12). A product with `affordable: false` is disabled and stays in the list.
  2. After a tap on a product: "Turn {price_cr} tokens into a voucher for {name}?" (the word follows the string, §10), the line "Valid for {voucher_expiry_days} days. An unused voucher is refunded." with the days from `/config`, a primary "Get voucher" and a ghost "Back". "Get voucher" sends the product's `id` as `product_id` to `POST /vouchers`.

  The answer is shown as the voucher: the `product_name` (`h2`), the QR tile holding `code`, the code as text, "Valid until" with `expires_at`, the steaming cup with "Show this at the bar. You earned it, sort of.", the brightness hint and a "Done" button. The balance becomes the answer's `balance_cr`, and the voucher is in the list behind the sheet.
- **Show QR** asks `POST /vouchers/{voucher_id}/code` for a fresh code and shows the same voucher view with the answer's `code` and `expires_at`. Every tap gives the voucher another code, and a code shown before stops working. An answer of `CONFLICT` (the voucher is used up or has expired) or `NOT_FOUND` shows the API's `message` in the sheet and refetches the vouchers.
- **A voucher code lives on screen only.** The API sends it once, in the answer that issued the voucher or gave it a fresh code. The Wallet holds it in memory while the voucher view is open and writes it to no browser storage. Closing the view forgets it, and "Show QR" brings another.
- "Get a voucher" is enabled when `can_get_voucher` in `GET /me` is true. When it is false the button is disabled, with a line under it: "You do not have enough tokens for a voucher yet." The frontend looks at neither the balance nor at what a product takes; `can_get_voucher` and `affordable` are the server's answers (SPEC A.11).
- **An issue without an answer.** When `POST /vouchers` gets no answer (§14), the sheet closes, a neutral banner reads "We did not get an answer. If your voucher was issued, it is in the list below.", and the Wallet refetches `GET /me/vouchers` and `GET /me`. "Get a voucher" stays disabled until both have answered. A voucher that is in the list was issued, and "Show QR" gives it a code.
- The product list is a list, not a shop: no images, no basket, no quantities (§2, principle 7).

States: skeletons for the balance card, two voucher rows and five ledger rows while loading. Error: a banner with "Try again". An Activity without rows shows the empty cup and "Nothing here yet."

### Leaderboard (`/leaderboard`)

Modelled on Polymarket's leaderboard, with the top three turned into a podium. Every player with a profile is on it. The server ranks, sorts and filters; the screen sends the choice and shows the answer in the order it arrives (golden rule 6).

1. Top bar.
2. Title "Leaderboard" (`h1`) and one `caption` line that states the measure the list is sorted by:

   | `sort` | Chip | Figure | Caption |
   | --- | --- | --- | --- |
   | `credits` | "Tokens" | `credits_cr` | "Tokens held right now." |
   | `win_rate` | "Win rate" | `win_rate` | "Share of resolved markets that returned more than was put in." |
   | `trades` | "Trades" | `trades` | "Number of trades made." |
   | `avg_payout_ratio` | "Payout" | `avg_payout_ratio` | "Tokens returned per token put in, over resolved markets." |

3. **Sort row:** the four chips (filter chips, §12), one of them selected. At the right end of the row, an icon button that flips `order` between "Highest first" (`desc`) and "Lowest first" (`asc`), with an `aria-label` that names the active one, and a secondary "Filter" button with a count badge when filters are set. The list opens on "Tokens", "Highest first".
4. **Filter sheet:** a Level select and a Programme select from `/config` `degrees` (`degree_level`, `degree_programme`), then one row per measure with a "from" and a "to" field:

   | Row | Sent as | Typed as |
   | --- | --- | --- |
   | "Tokens" | `min_credits_cr`, `max_credits_cr` | An amount; the string that was typed is sent |
   | "Win rate" | `min_win_rate`, `max_win_rate` | A percentage; sent as a fraction by moving the decimal point two places in the string |
   | "Trades" | `min_trades`, `max_trades` | A whole number |
   | "Payout" | `min_avg_payout_ratio`, `max_avg_payout_ratio` | A number such as 1.5 |

   A primary "Show results" and a ghost "Clear all". Set filters appear under the sort row as removable chips ("BSc Computer Science", "Win rate from 50%").
5. **Podium**, in a "Highest first" list: three columns on one `--card`, for the rows of `rank` 1, 2 and 3. First place is in the middle and stands highest, second on the left, third on the right. Each column shows the trophy (96 px for first, 72 px for the others), the rank as text ("1st", "2nd", "3rd"), the `username` (`h3`, one line) and the figure of the sorted measure (`number-lg` for first, `number` for the others). The "steps" are flat `--raised` blocks of three heights with square top corners: pixel-art blocks, not rounded cards.
6. **Rows:** every row that is not on the podium, as leaderboard rows (§12) of 56 px. More rows load with `next_cursor` as the user scrolls.
7. **Your row:** the row the API marks with `is_viewer: true` has a `--raised` background and the label "You"; on the podium the label stands under the username. The `viewer` row of the response is the viewer's place under the same sort and filters, wherever it ranks. It is pinned to the bottom of the screen, above the tab bar, whenever the viewer's row in the list is not on screen. When `viewer` is null the filters leave the viewer out and nothing is pinned.

From 768 px the rows below the podium are a table with all four measures as columns. A tap on a column heading sorts by it, a second tap flips the order, and the heading carries `aria-sort`.

Rules:

- Sort, order and every filter are query parameters of `GET /leaderboard` and live in the address under the same names (`/leaderboard?sort=win_rate&order=asc&degree_level=MASTER`), so a view can be shared and the back button works. The screen sends no `limit`: the page size is the API's. The frontend never reorders, filters, ranks or totals rows.
- Usernames, ranks and figures are shown as the API sends them. A real name or an email address never appears here.
- **Decision:** a podium place needs a figure. A place stays empty, as a `--subtle` outline of its step with no trophy, when the list has no row of that `rank`, or when that row has no figure for the sorted measure; such a row is listed below with the others. A "Lowest first" list has no podium and shows no trophy: a trophy is not for last place.
- A player with no resolved market has no win rate and no payout ratio: the figure is an en dash. The server sorts those players last in both directions and leaves them out as soon as a filter on either rate is set.
- A pinned row that is one of the three podium rows carries its 24 px trophy beside the username.
- A filter value the API rejects (`VALIDATION_ERROR`) is marked on its field in the sheet; the list keeps its last result. The frontend does not compare a "from" with a "to" itself.
- Each page is ranked when it is asked for, so a player can show on two pages or on none while balances move. The screen shows what it gets, and a refetch on focus puts it right.
- The bean drop (§7) plays once on the first-place figure when the viewer opens the unfiltered "Tokens" list while `viewer.rank` is 1. No other animation, no confetti.
- Without filters the list always holds at least the viewer. With filters and no row: "Nobody matches these filters." with a "Clear all" button. In a "Highest first" list whose first row has no figure for the sorted measure, the line "Nobody here has a resolved market yet. The podium is yours to take." stands above the empty podium. Loading: skeleton podium and five skeleton rows. Error: a banner with "Try again".

### Propose market (`/propose`)

An inner screen with the title "Propose a market". A single-column form, one field per row, in this order: "Question" (`question`), "YES means" (`yes_definition`), "NO means" (`no_definition`), "How it is decided" (`resolution_criteria`), "Source" (`resolution_source`), "Trading closes" (`close_at`), "Decided by" (`resolve_by`), "Liquidity" (`requested_tier`).

- Each text field shows its character limit from `/config`: `question_min_length` to `question_max_length` for the question, `short_text_max_length` for the two definitions and the source, `long_text_max_length` for how it is decided.
- The two times are entered and shown in the `/config` `timezone` and sent as ISO 8601 with their offset. Their limits (`max_market_lifetime_days`, `max_resolution_delay_days`) are stated in help text from `/config`.
- The tier is three radio cards from `/config` `tiers`, each with a plain name and one line of explanation:

  | `tier` | Name | Explanation |
  | --- | --- | --- |
  | TIGHT | "Tight" | "The price moves fast. For a question few people will trade." |
  | DEFAULT | "Default" | "Right for most questions." |
  | MEDIUM | "Medium" | "The price moves slowly. For a question many people will trade." |

- Above the submit button, a short list of the content rules of SPEC §6 (no private matters of named people, no grades, no outcomes one trader can decide, no party politics or religion) and the notes "You cannot trade a market you propose." and "You can propose {max_proposals_per_day} markets a day."
- Primary "Submit for review", which sends `POST /markets`. Validation errors from the API are placed on their fields using `details`; `LIMIT_EXCEEDED` shows the API's `message` above the button. Success shows the toast "Proposal sent", empties the form and puts the proposal at the top of the list below.
- Below the form, **My proposals** from `GET /me/proposals`: the question, a status badge and, when rejected, `rejection_reason`. A proposal whose `status` is neither PROPOSED nor REJECTED is a link to its market. With none: the blackboard illustration and "No proposals yet."

### Admin (`/admin`, `/admin/markets/[id]`, `/admin/codes`, `/admin/risk`)

For role ADMIN; anyone else sees the "not found" screen. Three tabs under the top bar, each a link to its route: **Markets**, **Codes**, **Risk**. Beside them a "Scan vouchers" link leads to the Barista screen. Designed for 1024 px; usable on a phone with horizontal scrolling tables. Density is higher here: rows are 44 px and `body-sm`.

- **Markets (`/admin`):** a "Proposals" queue first, from `GET /markets` with `status` PROPOSED, then all markets with a status select. Each choice of the select sends its one `status`; "All" sends all seven, because a request without `status` leaves PROPOSED and REJECTED out. A row shows `question`, the status badge, `p_yes`, `volume_cr` and `close_at`, and opens the admin view of the market.
- **One market (`/admin/markets/[id]`):** an inner screen from `GET /markets/{market_id}`: the question, the status badge, the rules as on Market detail, the `resolution` when there is one, and the facts only an admin needs: `proposer_email`, `requested_tier`, `tier`, `b_cr`, `max_loss_cr`, `created_at`, `opened_at`, `closed_at` and `rejection_reason`. Tiers are named "Tight", "Default", "Medium" and "Custom". Then an action bar with the actions the market's `status` allows (SPEC §6):

  | Action | For `status` | Asks for | Sends |
  | --- | --- | --- | --- |
  | Approve | PROPOSED | A tier: one of the three of `/config` `tiers`, each with its `b_cr` and `max_loss_cr`, or Custom with a budget of at most `max_market_budget_cr`. Optional changes to the wording and the two times | `POST /admin/markets/{market_id}/approve` with `tier`, `budget_cr` for CUSTOM, and each field that was changed |
  | Reject | PROPOSED | A reason, which the proposer sees | `POST /admin/markets/{market_id}/reject` with `reason` |
  | Halt | OPEN | A reason | `POST /admin/markets/{market_id}/halt` with `reason` |
  | Resume | HALTED | A note, optional | `POST /admin/markets/{market_id}/resume` with `note` |
  | Close | OPEN, HALTED | A note, optional | `POST /admin/markets/{market_id}/close` with `note` |
  | Resolve | CLOSED | The outcome, YES or NO, and the evidence | `POST /admin/markets/{market_id}/resolve` with `outcome` and `evidence` |
  | Void | CLOSED | A reason, which is published as the market's resolution | `POST /admin/markets/{market_id}/void` with `reason` |

  - Only the actions of the market's status are shown. Each opens a dialog that asks for what its row names; text limits come from `/config` (`short_text_max_length` for a reason or note, `long_text_max_length` for the evidence and for the reason of a void).
  - Reject, Close, Resolve and Void cannot be taken back. Their dialog is an `alert-dialog` that states the consequence, and the confirm button of Reject and Void is `destructive`. Reject: "The proposer sees your reason. A rejected proposal stays rejected." Close: "Trading stops for good. After this the market can only be resolved or voided." Resolve: "Everyone who holds the winning outcome is paid. This cannot be changed afterwards."
  - **Who may settle.** Resolve and Void follow `viewer.can_settle`. When it is false, both buttons are disabled and a neutral banner above the action bar reads "You traded this market, so another admin has to settle it.", the sentence the API answers both actions with. If one of them is answered `FORBIDDEN` all the same, the dialog shows the API's `message` and the market is refetched.
  - **The void dialog** shows the market's `void_rule` as the consequence and, labelled "What a void costs VIS now", the `void_cost_cr` of this market's row in `GET /admin/risk` (the entry of `markets` with the same `id`), read afresh when the dialog opens. While that figure loads it is a skeleton and the confirm button is disabled; if it cannot be read, the dialog shows the banner of §14 and cannot be confirmed.
  - The answer of every action is the market; the screen updates from it. `CONFLICT` and `LIMIT_EXCEEDED` show the API's `message` in the dialog, and the market is refetched.
- **Codes (`/admin/codes`):** a table of batches from `GET /admin/code-batches` (`label`, `value_cr`, `count`, `active_count`, `redeemed_count`, `revoked_count`, `expires_at`) and "New batch". The dialog asks for `label`, `count`, `value_cr` (filled in with `code_default_value_cr`, with the help text "Between {code_min_value_cr} and {code_max_value_cr} tokens"), `expires_at` and "One code per person" (`one_per_user`), and sends `POST /admin/code-batches`. After creation, the codes are shown **once** with "Download CSV" and "Print QR sheet", both generated in the browser (SPEC A.6), under a banner: "These codes cannot be shown again. Save them now." Leaving the page asks for confirmation. Each QR of the sheet holds the `redeem_url` of its code. "Revoke" on a batch opens an `alert-dialog` ("Every code of this batch that has not been redeemed stops working.") with a `destructive` button and sends `POST /admin/code-batches/{batch_id}/revoke` without `codes`. Nothing here names a price for a code.
- **Risk (`/admin/risk`):** every figure is a field of `GET /admin/risk`, shown as sent; the page compares nothing.

  | Tile | Figure | Beside it |
  | --- | --- | --- |
  | "Obligations" | `obligations.total_cr` | "Alert above" with `obligations.alert_threshold_cr`, and its three parts: "Balances" `user_balances_cr`, "Vouchers not used yet" `pending_vouchers_cr`, "Markets" `market_exposure_cr` |
  | "Loss budget" | `loss_budget_cr` | "of" `max_loss_budget_cr` |
  | "Open markets" | `open_markets` | "of" `max_open_markets` |
  | "Codes not redeemed yet" | `unredeemed_code_value_cr` | |
  | "Codes this month" | `code_issuance_this_month_cr` | "of" `max_code_issuance_per_month_cr` |

  - The Obligations tile is outlined in `--no` with an alert icon when `obligations.above_threshold` is true. The other tiles show their figure and its limit side by side with no warning state, because the API sends no flag for them and the frontend does not compare figures itself.
  - Then the alerts nobody acknowledged yet (`alerts`): `message`, `created_at` and the keys and values of `context`, each with an "Acknowledge" button that sends `POST /admin/alerts/{alert_id}/ack`. Alert `severity`: INFO and WARNING are neutral banners with different icons, CRITICAL uses the error banner.
  - Then the table of `markets`, one row per market that is not settled, in the API's order: `question`, the status badge, `tier`, `p_yes`, `volume_cr`, `q_yes` and `q_no` as shares, `b_cr`, `budget_cr`, `account_balance_cr`, `exposure_cr`, `void_cost_cr` and `close_at`. A row opens the admin view of the market. A figure the API sends with a minus sign keeps it.

States: every table has skeleton rows while loading and a banner with "Try again" on error. An empty table is one plain sentence: "No proposals waiting.", "No markets.", "No batches yet.", "No alerts." The risk page refetches on focus; nothing on it is live.

The print sheet is the one light surface in the product, because it is paper: `--ink` on unprinted paper, `logo-lockup-dark.png` at the top, a grid of QR codes with the code and value under each.

### Barista (`/barista`)

Built to be used at arm's length, fast, by someone holding a cup. For roles BARISTA and ADMIN; anyone else sees the "not found" screen.

1. The scanner fills the screen below the top bar. A "Type the voucher code" button is below it.
2. A scan, or a typed code, is sent as `code` to `POST /barista/vouchers/redeem`. The scanner pauses until the answer is there, then a full-screen result shows for 2.5 s and the scanner returns:
   - **Accepted:** `--yes` background, `--ink` text, a large check icon, the `product_name` in `display`, then "Voucher accepted" and the token icon with `value_cr` in `number-lg`, and at the bottom "Paid in predictions." The product is the largest thing on the screen: it is what the barista hands over.
   - **Rejected:** `--no` background, `--ink` text, a large cross icon and "Voucher not valid". No reason is given (SPEC A.6). This is the result for `CODE_INVALID` and for a `VALIDATION_ERROR`, which is a scan that cannot be a voucher code.
3. Each result also triggers a short vibration where supported: one pulse for accepted, two for rejected.
4. Any other failure is no verdict on the voucher. It shows the banner of §14 over the scanner, never the red screen, and the barista scans again. The API cannot say what became of a scan that got no answer: if that scan did redeem the voucher, the next one answers "Voucher not valid".

This is the only screen that uses a full-bleed green or red surface; the result must be readable from across a counter.

### Not found (any other path)

Inside the app shell: the Polybahn car, "This page does not exist. Not even in the HG basement." and a secondary "Browse markets" button. It stands for a path the app does not have, for a market the API answers `NOT_FOUND` for, and for the Admin and Barista routes opened without their role.

## 14. States and feedback

### Every screen has four states

Loading (skeletons), empty (icon, sentence, at most one button), error (banner with "Try again") and content. A screen is not finished until all four exist.

### Errors

The API returns `{code, message, details}`. The `message` is written for the user and is shown as sent; the frontend branches on `code` only to decide where and how to show it.

| Code | Where it appears | Treatment |
| --- | --- | --- |
| `PROFILE_REQUIRED` | Start-up | The profile set-up screen (§13), with `details.name` and `details.email`. No error styling: the visitor has no profile yet |
| `USERNAME_TAKEN` | Profile set-up, Profile | On the username field, with the API's message |
| `QUOTE_STALE` | Trade sheet | New quote from `details.quote`, inline notice, confirm again |
| `INSUFFICIENT_BALANCE` | Trade sheet, voucher sheet | Inline above the button; the voucher sheet also refetches the products |
| `INSUFFICIENT_SHARES` | Trade sheet (sell) | Inline above the button, then refetch the market so the sell presets are current |
| `LIMIT_EXCEEDED` | Trade sheet, propose form, redeem sheet, admin dialogs | Inline; on trades, refresh the stake-left line from `details` |
| `TRADE_TOO_SMALL` | Trade sheet | Inline above the button |
| `MARKET_NOT_OPEN` | Trade sheet | Inline, then refetch the market so the screen shows its real status |
| `CODE_INVALID` | Redeem sheet, barista | Inline on the field; the rejected screen on the scanner. Always the same wording |
| `RATE_LIMITED` | Redeem, trade; any request when the proxy's own limit is hit | Inline where an action was refused, otherwise the banner; the action is disabled for `details.retry_after_seconds` with a countdown |
| `VALIDATION_ERROR` | Forms, leaderboard filters, barista | On the field named in `details`: `details.field`, or the last element of `loc` in an entry of `details.errors`. Without a field, above the submit button. On the scanner, the rejected screen |
| `UNAUTHENTICATED` | Anywhere | The full-screen message (§13) with the API's message and "Try again", which reloads. The request reached the API without the portal's identity; no screen can repair that |
| `FORBIDDEN` | Anywhere | The API's message as a banner, or inline in the sheet or dialog that asked. At start-up (an address MarkETH is not open to, a suspended account) the full-screen message |
| `NOT_FOUND` | Routes, "Show QR" | The "not found" screen. For a voucher, inline in the sheet, then refetch the vouchers |
| `CONFLICT` | Admin actions, "Show QR" | Inline in the dialog or sheet, then refetch. For a voucher it means the voucher is used up or has expired |
| `IDEMPOTENCY_KEY_REUSED` | Trade sheet | Should not occur. Show "Something went wrong. Try again." in place of the message and create a new key for the next attempt |
| `SERVICE_UNAVAILABLE` | Anywhere | Banner with the API's message ("This is temporarily unavailable. Try again shortly."). Code redemption needs Redis; trading does not |
| `INTERNAL_ERROR` | Anywhere | Banner with the message and "Try again" |

Two answers are not the API's and are told apart by their body. Every API call asks for JSON (`Accept: application/json`), and the portal answers such a call it has no session for with a 401 whose body is the empty object `{}`. That 401 means reload: the page reloads once, the portal signs the visitor in and returns to the same address. If the reload brings the same answer, the full-screen message of §13 is shown. Any other answer without a `code` (an error page of the proxy, a body that is not JSON) is treated as `SERVICE_UNAVAILABLE`, with the banner "This is temporarily unavailable. Try again shortly."

A network failure with no response uses the same banner: "No connection. Try again."

### A submitted trade

A trade carries an `Idempotency-Key`: the same key with the same body returns the same trade and never executes a second one (SPEC §8). The trade sheet therefore keeps the two together.

- Confirm serialises the request body once, makes one key for it and keeps both.
- Both are kept until an answer says what became of that request: a 2xx (the trade exists) or a 4xx other than 429 (the trade was refused and nothing was booked). Then they are dropped, and the next Confirm builds another body with another key. `QUOTE_STALE` is such a 4xx: the quote of `details.quote` leads to a different body.
- Every other outcome says nothing about the trade, and both are kept: any 5xx, a 429, the portal's 401 and a request without a response. The message is shown inline, after a 429 with the countdown of `details.retry_after_seconds`, and the button reads "Try again". A tap sends the stored body with the stored key again, unchanged. A body rebuilt from a later quote would be another request and could trade a second time.
- **Decision:** while the two are kept, the sheet offers nothing but "Try again". The Buy / Sell control, the outcome toggle and the presets are disabled, and closing and reopening the sheet shows the same unanswered trade. Nothing is sent again without a tap.
- They are held in memory, not in browser storage. A reload forgets them, and so does the reload that follows the portal's 401; the market, the balance and the History then show what the server recorded.

### Other writes without an answer

A write whose answer never arrived (no connection, a 5xx) may or may not have happened. The screen does not guess and does not send it again by itself: it refetches what the write would have changed, shows that, and only then lets the user act again.

| Write | Before the user may send it again |
| --- | --- |
| `POST /vouchers` | `GET /me/vouchers` and `GET /me` are refetched (§13, Wallet). A voucher that was issued is in the list, and "Show QR" gives it a code |
| `POST /codes/redeem` | `GET /me` and `GET /me/ledger` are refetched: the balance and the Activity show whether the code counted |
| `POST /markets` | `GET /me/proposals` is refetched: a proposal that arrived is in the list |
| `POST /admin/code-batches` | `GET /admin/code-batches` is refetched. A batch that is in the table without its codes having been shown is revoked and created again, because the API shows codes only in the answer that created them |
| Another admin action | The market or the risk page it acts on is refetched |
| `POST /vouchers/{voucher_id}/code`, `PUT /me/profile` | Nothing: sending either again is harmless |
| `POST /barista/vouchers/redeem` | Nothing can be refetched; §13 says what the scanner shows |

### Live data

- Market detail subscribes to `GET /markets/{market_id}/stream`. A `quote` event updates the Price, the chart and the volume from its `p_yes`, `p_no` and `volume_cr`; a `status` event refetches the market.
- Events are hints, not truth: one whose `version` is not newer than what is on screen is dropped.
- While **Your position** is shown, a `quote` event also refetches the market, because what a holding is worth (`value_cr`) and the sell presets are the server's to work out.
- If the stream ends or answers an error, the screen polls every `live_poll_fallback_seconds` from `/config`, tries the stream again at each poll and keeps working. There is no error banner for this; the user sees slightly slower updates. The first poll doubles as a check of the portal's session: if the portal answers it with its 401, the page reloads (§ Errors).
- An open quote in the trade sheet is not replaced by a stream event. The user confirms what they were shown, and the server answers `QUOTE_STALE` if it has moved.
- Balances can change between visits (monthly tokens and voucher refunds are booked lazily, SPEC A.3), so the balance is refetched whenever Wallet or the trade sheet opens.
- Nothing else is live. Every other screen refetches what it shows when it opens and when the window regains focus.

### Buttons that change state

Every button that posts is disabled from tap until response. Destructive admin actions additionally go through a confirmation dialog. Nothing in the product is submitted by a page load or a prefilled field alone: there is always a deliberate act, which is a tap or, at the bar, the scan of a voucher.

## 15. Accessibility

- **Contrast:** text meets WCAG AA using the pairs in §4. A new colour pair is measured before it is used.
- **Colour blindness:** green and red always come with a word, a sign or an icon. The outcome buttons keep a fixed order, YES on the left.
- **Focus:** a 2 px `--ring` outline on `:focus-visible` for every interactive element. Sheets and dialogs trap focus and return it to the opener on close.
- **Semantics:** one `h1` element per screen: the title in the top bar of an inner screen, the question on Market detail and on the admin view of a market, the title of a main screen that shows one (Leaderboard), and otherwise a visually hidden one that names the screen. The styles `h1` to `h3` of §5 are looks, not levels. The tab bar is a `nav`; the outcome toggle and the filter chips use `aria-pressed`; a column heading that sorts carries `aria-sort`; form errors use `aria-describedby` and `aria-invalid`.
- **Live regions:** the live Price is in an `aria-live="polite"` region limited to one announcement per 10 s. The `QUOTE_STALE` notice and trade errors use `role="alert"`.
- **Screen-reader text for figures:** the Price reads "YES 54.8 percent". An amount reads "25 tokens". A missing rate reads "none yet", not the dash.
- **Chart:** an `aria-label` summarises it ("YES probability over time, now 54.8 percent, opened at 50 percent"), and the same information is always present as text on the screen.
- **Text size:** layouts hold at 200% browser zoom and with large system text; type is set in `rem`.
- **Motion:** `prefers-reduced-motion` is honoured (§7).
- **Camera:** every scan has a typed alternative.

## 16. Review checklist

Do:

- Ask of every new element: is it Polymarket, or is it a pixel-art or campus touch? If neither, leave it out.
- Use tokens and the listed components; add a component here before adding it in code.
- Show the API's strings and messages as sent.
- Give every screen its loading, empty and error state.
- Keep one white primary button per screen.
- Check each new screen at 375 px, at 200% zoom and with the keyboard.

Don't:

- Compute, compare or round a price, balance, limit or share quantity in the frontend. The one test an amount gets is the one for the string `0` (§10).
- Hard-code a tunable ("100 tokens", "35 tokens", "30 days") in copy or logic.
- Use green or red for anything that is not YES, NO, a gain, a loss, an error or a destructive action.
- Put white text on a green or red fill, or pure red text on a card.
- Add a gradient of any kind, a light theme, a shadow on a card, a blur, or a remote image or font.
- Build anything from the "must not look like" table in §1: landing sections, marketing copy, glass, blobs, sidebars, pill-shaped everything, confetti.
- Use a trophy JPEG, or a trophy for anything but one of the first three places of a "Highest first" leaderboard.
- Use the ETH logo, or type the name beside the logo with name.
- Use pure white `#FFFFFF` or pure black `#000000` anywhere on screen.
- Put a joke in an error, a balance, the trade sheet or the admin area, or more than one campus line on a screen.
- Add any screen, button or wording about buying tokens, codes or vouchers, or name a price for one.
- Show "CR", "credits" or "vouchers" as the unit's name, or the bean without its white disc.
- Say why a code or voucher was refused.
- Show an email address or a real name outside the viewer's own profile and the admin area.
- Sort, filter, rank or total leaderboard rows in the frontend, or look at the balance to decide whether a product can be had.
- Build a sign-in, a sign-out or a signed-out state. The session belongs to the portal.
- Keep a code or a voucher code in browser storage, or leave a code in the address.
- Rebuild the body of a trade that got no answer, or give it another Idempotency-Key.
- Leave a `dark:` class, a shadow or a raw colour in a component copied from shadcn/ui.
