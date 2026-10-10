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
| A landing page: hero headline with subheading and call-to-action, "how it works" in three steps, feature grid of icon cards, testimonials, logo wall, FAQ, pricing, newsletter, big footer | The first screen is the Markets list, as on Polymarket. The one thing that can stand in front of it, once, is the profile set-up: a logo, three fields, one checkbox and one button |
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
| Trade ticket: Buy / Sell tabs, outcome buttons that carry their price, a size field with shortcut chips, one wide action button | §13 |
| The Markets list as the home page, with no landing page in front of it | §11, §13 |
| Logo with the name at the top left of every main screen | §3, §11 |
| Bottom tab bar on phones; a ticket panel beside the chart on desktop | §6, §11 |
| White percentage figures; green and red kept for the Yes and No controls | §4 |

| Deliberately different | Why |
| --- | --- |
| No profit figure and no cents | The quote says what an order costs and what its shares pay; the API sends no difference of the two, and the frontend may not compute one (golden rule 6) |
| No deposit, cash-out or payment screens | Nothing is for sale (golden rule 9) |
| No sign-in, sign-up or sign-out | The portal in front of the app signs every visitor in before a page loads (§11) |
| No comments, categories, search or market images | Not in the API (SPEC A.1) |
| A leaderboard with pixel trophies for the top three | Polymarket's leaderboard, made into a podium (§13) |
| A coffee-bean token in place of the dollar sign | The unit is a token; enough of them turn into a coffee at the bar |
| Pixel-art campus touches and ETH in-jokes (§3) | Polymarket is neutral; MarkETH belongs to one campus |
| A buy is sized in shares, with a slider beside the field; "Half" and "All" when selling | A share pays one token, so a number of shares says what an order can pay, and its price is a decimal anybody can read. The range of the slider, the shortcuts, what a sale brings in and the verdict on a typed number are the server's (§12, §13) |

## 2. Principles

1. **Phone first.** Every screen is designed at 375 px wide and one thumb. Wider layouts are the phone layout with more room, never a different product.
2. **Numbers are the content.** The probability is the largest thing on any screen that has one. Chrome stays quiet so numbers can be loud.
3. **The server owns every number.** The frontend formats what the API returns and never computes a price, balance, limit, profit, rank, order or eligibility (root `CLAUDE.md`, golden rule 6). If a screen needs a number the API does not send, the screen does without it.
4. **Calm navy, scarce colour.** The canvas is navy, the accent is the off-white `#FAFAFF`. Green and red appear only where they mean YES, NO, gain or loss.
5. **One primary action per screen.** There is at most one white button visible at a time.
6. **Serious numbers, playful edges.** The trade ticket, balances and errors are sober. Personality lives around them: the bean, the pixel art, a campus joke in an empty state (§3).
7. **Nothing is for sale.** No screen, button or sentence suggests buying tokens, codes or vouchers (golden rule 9, SPEC §2). Tokens are given, a code is redeemed, and tokens are turned into a voucher: the wallet says "get", never "buy", "price" or "order". Those words belong to shares alone, where the trade sheet uses them ("Buy YES", "Price per share", "The price moved."). Avoid "top up", "deposit", "cash out" and any currency symbol everywhere.

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
| `logo-lockup.png` | Logo with name in `#FAFAFF`, transparent, 716 x 168 px | Top bar (28 px tall), profile set-up and the full-screen message (48 px tall), the cards of the printable QR sheet (5 mm tall) |
| `logo-lockup-dark.png` | Logo with name in `--ink`, transparent | The head of the printable QR sheet only |
| `logo-mark.png` | Logo in `#FAFAFF`, transparent, 320 x 320 px | Where only a square fits, for example a narrow header beside a back arrow |
| `logo-mark-dark.png` | Logo in `--ink`, transparent | The printable QR sheet only |
| `icon-512.png`, `icon-192.png` | `#FAFAFF` logo on `#1E2749`, square | Web manifest (home-screen icon) |
| `apple-touch-icon.png` | The same, 180 x 180 px | iOS home-screen icon |
| `favicon.png` | The same, 48 x 48 px | Browser tab |
| `token.png` | The token, transparent, 256 x 256 px | The token icon (below) |
| `trophy-1.png`, `trophy-2.png`, `trophy-3.png` | Gold, silver and bronze pixel trophies, transparent, 256 x 256 px | First, second and third place on the leaderboard (below) |

Three files lie in `frontend/public/` itself, beside `brand/`: `favicon.ico` (the 48 px favicon as an ICO file), `apple-touch-icon.png` and `apple-touch-icon-precomposed.png` (both the 180 px icon of `brand/apple-touch-icon.png`). The page declares its icons under `/brand/`, and a browser asks for these three addresses at the root of the site all the same, Safari on iOS with every visit. They are copies of the brand files, served by the app itself, and are regenerated with them.

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
- Sizes: 96 px for first place and 72 px for second and third on the podium; 24 px beside a username in a leaderboard row. Never larger than 96 px, never smaller than 24 px, and on the podium never wider than the column it stands in.
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

- At most one campus line per screen, with its illustration where it has one, and none inside the trade sheet, a form in progress, the usage policy, or the admin area. The token, the trophies and the bean animations of §7 are the product's own look and are not counted.
- Errors, balances, limits and anything about why a trade or code failed are never joked about. They show the API's message.
- References are to places and shared experiences (HG, CAB, Polyterrasse, Polybahn, Mensa, ASVZ, exam session). Never to named people, departments' reputations, grades of anyone, politics or religion: what the usage policy keeps out of markets (§13, SPEC §6) stays out of our copy too.
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
| `--radius-md` | 10 px | Buttons, inputs, shortcut chips |
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

- **A live figure:** when a figure changes by itself (a price, a figure of the leaderboard, the balance in the top bar), its background flashes `--raised` for 600 ms and fades. The flash is neutral, not green or red, so a busy market does not strobe colour.
- **Chart:** a new point extends the line over 200 ms. The chart never re-animates from zero on an update.
- **Sheet:** slides up from the bottom while the scrim fades in; it can be dragged down to dismiss.
- **Loading:** skeleton blocks in `--raised` with a slow opacity pulse (1.2 s). No spinners except inside a button that is waiting for its response.
- **Bean drop:** when tokens arrive in front of the user, three to five token icons fall into the figure that grows over 600 ms, then the figure updates. Three arrivals count: the welcome tokens (into the amount of the welcome dialog), a redeemed code (into the balance card of the Wallet) and a payout that comes while its market is on screen (into "Paid out", §13). It plays once per arrival and never on page load: tokens booked while nobody was looking (monthly tokens, a refund, the payout of a market that was not on screen) simply show in the balance. A payout that arrives while another screen is open flashes the pill of the top bar and drops no beans. It also plays once for a viewer who opens the leaderboard in first place (§13).
- **Bean bounce:** the token icon of the amount in a trade's receipt banner bounces once (200 ms) when the banner appears.
- **Polybahn loader:** the progress line and its car (§3). It never blocks input.
- With `prefers-reduced-motion: reduce`, movement is removed: the bean drop, bean bounce and Polybahn car are not shown, the sheet and toasts cross-fade, the flash and the pulse are disabled.

## 8. Icons and imagery

- Icons are `lucide-react`, the set shadcn/ui ships with. Stroke 2 px; 20 px inline, 24 px in bars.
- An icon alone is never a control's only label, except the back arrow, the close cross and the tab bar, which all carry an `aria-label` (the tab bar also shows text).
- Icons are `--foreground` or `--muted-foreground`. An icon is green or red only inside an element that already is.
- Markets have no images and there is no photography. Illustration is pixel art only, from the short list in §3; empty states use one illustration (or a 32 px icon until it is drawn) and a sentence.
- The only raster images are the logo, the token icon, the trophies, the pixel illustrations (§3) and generated QR codes.
- A brand image is fetched once per page session. However many components show the token icon, a logo, a trophy or an illustration, and however often they mount, render again or scroll into view, the browser asks the server for each file at most once between two page loads, and an image that has loaded is in its element when that element is first painted: no empty disc and no flash on a later mount. The files stay in `public/brand` on the app's own origin, and a file that changed reaches a browser by the next page load after a deployment.

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
| Price per share (`average_price` of a quote or a trade) | Number in [0, 1], already rounded | The token icon, then the decimal: (bean) `0.524`. In a sentence it is followed by "per share" |
| What shares pay (`payout_cr` of a quote), what a sale brings in (`proceeds_cr` of a sell shortcut) | Decimal string | The token icon, then the string: (bean) `50` |
| Signed amount (ledger `amount_cr`) | Signed decimal string | Sign, token icon, figure: `+` (bean) `200` in `--yes-text`, `−` (bean) `25` in `--foreground` |
| Date and time | ISO 8601 | In the `/config` `timezone`, as `14 Oct 2026, 18:00`. An instant keeps its time of day also where the day alone would do: `next_monthly_grant_at`, `policy_accepted_at`. The time of day stands alone, as "18:02", in one place: the Barista's result for a voucher that was redeemed a moment ago (§13) |
| Deadline (`close_at`) in a list | ISO 8601 | Relative when under 7 days ("Closes in 3 days", "Closes today, 18:00"), otherwise the date. When `trading_open` is false, the date alone |
| Code | `code_length` characters (`/config`) | Groups of four, `XXXX-XXXX-XXXX`, Barlow, letter-spacing 0.08em |
| Voucher code | `voucher_code_length` characters (`/config`) | `XXXX-XXXX`, same style |
| Product (`name`, `price_cr`) | String and decimal string | The name as sent, then the token icon and the string: Coffee (bean) `35` |
| Rate (`win_rate`) | Number in [0, 1], already rounded, or null | A percentage like a probability: `62.5%`. Null is an en dash |
| Ratio (`avg_payout_ratio`) | Number, already cut to two decimals, or null | Two decimals and a multiplication sign: `1.35×`. Null is an en dash |
| Count (`trades`, `rank`) | Integer | Digits grouped in threes: `1,204` |
| Share range (`slider` of an entry of `viewer.buy`) | Three integers in whole shares, `min`, `max` and `step`, or null | The range of the slider of the shares entry (§12) and nothing else. The slider writes the step it stands on into the shares field as plain digits: `40` |
| Degree (`degree_level`, `degree_programme`) | A level and the label of a programme | The programme as sent: "BSc Computer Science". A level is shown by its `name` from `/config` `degrees` |

Rules:

- **Amounts are strings.** Never pass a `*_cr` or `shares` value through `parseFloat` or `Number`. An amount the user types (an adjustment of tokens, a bound of a filter) is a string as well, and so is a number of shares: each is sent with the characters that were typed. Where a list needs a shorter figure, cut the string after the second decimal place (cutting, not rounding, so the app never shows more than the user has). The full string is shown on detail views: the receipt banner of a trade, the expanded ledger row and the statistics of a player on the Leaderboard (§13). **Decision:** from 1,000 up, the whole part is grouped in threes with a comma, by inserting the commas into the string.
- **Zero is the one thing read from an amount.** The API trims zeros, so an amount of zero always arrives as the single character `0`. A screen may test for that string to leave out what would be empty (a holding of no shares, a market the viewer never bought into, a payout of nothing). **Decision:** this is a test of the string, not arithmetic. A screen never orders two amounts, adds them or measures one against a limit: `allowed`, `affordable`, `can_trade`, `can_get_voucher` and `can_settle` are the server's answers to those questions, and a number of shares that was typed is judged by the quote the server gives for it (§13). The three integers of a `slider` in `viewer.buy` are no exception: the server sends them so that a slider can be drawn, and the slider is all that reads them.
- **Percentages** are formatted with `Intl.NumberFormat("en-GB", { style: "percent" })` and the `/config` decimals. `p_no` comes from the API; the frontend does not subtract. A price per share is formatted the same way without the percent style, with two decimals more.
- **Times** are shown and entered in the `/config` `timezone`, whatever the device is set to. A time the user enters is sent as ISO 8601 with its offset.
- **Tokens out are not red.** A trade or a voucher lowering the balance is normal, so negative ledger amounts are white. Red is for losses and errors.
- **No derived figures.** There is no profit, loss or return figure, because the API sends none. What the shares of an order pay if their outcome comes true is the quote's `payout_cr`, and what a sell shortcut brings in is its `proceeds_cr`: the server's figures, shown beside what the order costs and never taken from one another. What a position is worth, took and gave back are the API's `value_cr`, `cost_cr`, `proceeds_cr` and `net_stake_cr`, and after settlement `payout_cr` or `refund_cr`; a screen shows the ones §13 names, each labelled. The win rate and the payout ratio of the leaderboard are the server's figures too.
- **How a market is priced is VIS's business.** A market's tier, its liquidity figure and its loss bound (`tier`, `b_cr`, `max_loss_cr`) are sent to admins only and are shown in the admin area only. No card, market page, trade sheet, proposal form, list or sentence for players names any of them.
- **No hard-coded tunables.** The stake limit, the shortcuts, the range and the step of the shares slider, the welcome and monthly tokens, what a product takes, the days a voucher is valid, the lengths of a username, a code and a text field, the most codes a batch may have, and the list of degrees come from `/config` or from the response that shows them. Copy is written with placeholders: "of {stake_limit_cr} tokens", never "of 100 tokens".
- **Icon or word.** A standalone figure (balance, row amount, quote value, chip) gets the token icon and no word. A figure inside a sentence gets the word and no icon: "You got 240 tokens." The word follows the string: "token" when the string is `1`, otherwise "tokens". A number of shares is no amount of tokens: it never carries the token icon, and the word "shares" follows it wherever a label does not say it.
- **Names in code are unchanged.** The API, the database and the spec call the unit a credit (`*_cr`, "CR"). "Token" is its name on screen only; do not rename fields.
- **Keys are not copy.** `status`, `tier`, `role`, `degree_level`, `sort`, `side`, `severity` and the `kind` of a notice arrive as keys. The words for them are in this file (the badges of §12, the names in §13) or in `/config` (`degrees`). Only "YES" and "NO" are shown as they arrive.
- **Server text.** API messages, ledger labels and product names are shown as sent. They are written in this file's vocabulary ("Not enough tokens for this.", "Welcome tokens", "Voucher: Coffee"); wording is corrected in the backend, never on screen.
- **People.** A player is shown by `username` and degree. A real name or an email address is shown in two places only: the viewer's own, on the profile set-up and Profile screens, and in the admin area, which shows `proposer_email` on a market and `name` and `email` in the Users tab. No other screen, row, toast or message shows either.
- Locale is fixed to `en-GB` for numbers and dates. The minus sign is U+2212, the en dash of a missing figure U+2013 and the multiplication sign of a ratio U+00D7.

## 11. App shell and navigation

Nobody is signed out. The portal in front of the app signs every visitor in through ETH before a page loads, so the app has no sign-in screen, no sign-out button and no signed-out state. What the app asks of a first visitor is a profile (§13).

### Top bar

56 px tall, `--card`, sticky. Left: the logo with name (`logo-lockup.png`, 28 px tall, 16 px from the edge), linking to Markets. This is its fixed place on every main screen (Markets, Portfolio, Leaderboard, Wallet, Admin, Barista), as on Polymarket; from 768 px the tabs follow it on the same line. Right: the balance as a pill (token icon and `balance_cr` of `/me`, Barlow 600) linking to Wallet, then a profile button (the lucide `user-round` icon, 44 px target, `aria-label` "Profile") linking to `/profile`. On inner screens the left side is a back arrow and the screen title; the right side stays. The back arrow goes one step back in the history; on a screen that was opened directly it goes to Markets, and from the admin view of a market to Admin.

The pill shows what `/me` says. `/me` is read again whenever the server says that something of the caller's own changed, and on every navigation while that cannot be heard (§14). A payout, a refund or monthly tokens that are booked while the user looks at something else are therefore in the pill at once, whatever screen is open, and its figure flashes when it changes (§7). The pill is named "Wallet, {balance_cr} tokens"; §15 says what a screen reader hears when the balance moves.

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

The Leaderboard tab reads "Ranking" in the tab bar of a phone and "Leaderboard" in the top bar from 768 px, which is also the title of its screen: a fifth of a 320 px screen is 64 px wide, and no label of the tab bar is ever cut, also with five tabs.

The role comes from `MeResponse.role`. Everyone starts as a plain user. A barista, like a further admin, is appointed by an admin in the Users tab of the admin area (§13); the first admins are named in the server's configuration, and the API marks them `bootstrap_admin`. Admins reach the scanner from the Admin screen. There are never more than five tabs. The tab bar is hidden on the screens that stand in front of the app (start-up, profile set-up, the full-screen message, and the usage policy for a visitor without a profile), on the Barista scanner while the camera is live, and while the trade sheet is open.

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
| `/policy` | Usage policy | Inner, "Usage policy" | this file |
| `/admin` | Admin: proposal queue and markets | Main | this file |
| `/admin/markets/[id]` | Admin: one market and its actions | Inner, no title | this file |
| `/admin/codes` | Admin: code batches | Main | this file |
| `/admin/risk` | Admin: risk panel | Main | SPEC §12 |
| `/admin/users` | Admin: users, their roles and their tokens | Main | this file |
| `/barista` | Barista scanner | Main | this file |
| every route | Start-up, and in place of the route Profile set-up or the full-screen message | None | SPEC A.3 |
| any other path | Not found | Main | this file |

- A visitor without a profile sees the profile set-up screen in place of whatever route they opened and continues to that route once it is saved. The address is kept meanwhile, so someone who scanned a code card arrives in the Wallet with the code. **Decision:** `/policy` is the one route the set-up does not stand in front of, because the policy is read before it is accepted.
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
| `destructive` | `--no` fill, `--ink` label | Reject, void, revoke, suspend. Always behind a confirmation dialog |
| `yes` / `no` | `--yes` or `--no` fill, `--ink` label | Outcome buttons, and the Confirm button of a buy |

Height 48 px (56 px for the trade Confirm), `--radius-md`, `label` text style, full width on phones. A loading button keeps its width, shows a spinner in place of its label and is disabled.

### Text link

A link that is text: inside a sentence ("I accept the usage policy") or a line of its own ("Read the usage policy", "Usage policy"). `--foreground`, underlined, in the text style of the line it stands in, with a target 44 px tall. It is never green or red (§4). A link that opens in a tab of its own says so to a screen reader (§15).

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

The card itself links to the market; the two buttons are separate links inside it. This is Polymarket's card. The buttons use the soft tints, never the full `--yes` and `--no` fills, so a long list stays calm. A card says nothing about how its market is priced (§10).

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
| EXPIRED | "Expired" | `--raised` fill, `--muted-foreground` text |

The badge shows the `status` the API sends, which is already the effective status (an OPEN market past its close time arrives as CLOSED, and a proposal that was not decided before its close time as EXPIRED). Whether trading is possible is read from `trading_open`, never from the clock. The clock has one job: when the `close_at` of a market that is shown as open for trading, or of a proposal that is shown in review, passes, the screen asks the API again (§14) and shows what the answer says.

### Probability chart

Plots `p_yes` from `GET /markets/{market_id}/history` over time.

- A **step line** (each trade holds its price until the next), 2 px, `--yes`, with a `--yes` area fill at 12% opacity beneath it.
- The y axis is fixed from 0% to 100% with gridlines at 25, 50 and 75% in `--border`; labels are `caption`, `--muted-foreground`. The x axis shows the first and last date only.
- The chart ends at `until` of the answer (`HistoryResponse.until`): the server's now while the market can still trade, otherwise the moment trading ended. The last price holds as a flat line up to there, and the last date of the x axis is that moment. No clock of the device is read for the chart.
- The series always starts at the 50% opening point the API includes, so a new market shows a flat line, not an empty box.
- Height 200 px on phones, 280 px from 768 px. Touching the chart shows a vertical guide and a tooltip with the percentage and time.
- A `market.quote` event of the stream appends a point (§14). An event whose `version` is not newer than the last drawn one is ignored.
- Built on Recharts through shadcn/ui's `chart`. Accessible alternative in §15.

### Shares entry (shadcn `input`, and the shadcn slider)

How many shares a buy is for. A slider, a number field and a row of shortcuts set one number, and the field always shows what will be sent. A share pays one token if its outcome comes true (SPEC §2), so the number is also what the order can pay; what it costs is the quote's to say (below). The entry belongs to one outcome: it is drawn from the entry of `viewer.buy` whose `outcome` is the selected one. Top to bottom, 8 px apart:

1. The label "Shares" (`caption`, `--muted-foreground`).
2. One row, 48 px tall, with 12 px between its two parts:
   - **The slider** takes the width the field leaves: 219 px in the sheet on a 375 px phone, 262 px in the dialog and 202 px in the panel, whose 1 px border takes one from each side. It runs over the whole shares of the entry's `slider`, from `min` to `max` in steps of `step`: three integers the API sends for this control and for nothing else. A 4 px `--border-strong` track with `rounded-full` ends; the part up to the thumb is `--foreground`; the thumb is a 24 px `--foreground` disc inside a 44 px target. Arrow keys move it one step, Home and End take it to its ends. It is never green or red (§4). When `slider` is null there is no slider, and the field takes the whole row.
   - **The shares field** is 112 px wide: a form field (below) with its text right aligned in `number`. It takes digits and nothing else, so the field shows what is sent, and it opens the numeric keypad. It holds no token icon: a number of shares is not an amount of tokens (§10).
3. **The shortcuts:** a row of equal chips from the entry's `presets`, each showing its `shares` in `label` and under it the word "shares" in `caption` ("share" when the string is `1`). The row holds up to four chips, and with four each is 79 px wide on a 375 px phone, which is why the word has a line of its own. Height 48 px, `--radius-md`. Default: 1 px `--border-strong`. Selected: `--foreground` fill, `--primary-foreground` text. A shortcut with `allowed: false` is disabled. The frontend never decides this itself (SPEC A.11).
4. **The stake-left line** in `body-sm` `--muted-foreground`: "{stake_left_cr} of {stake_limit_cr} tokens left in this market". The stake limit counts tokens put in, whatever the number of shares.
5. **The refusal**, while the server refuses the number (§13): the API's `message` in `body-sm` `--no-text` beside an alert icon, and the border of the field in `--no`.

The slider and the shortcuts are two ways of filling the field:

- Moving the slider writes the step it stands on into the field as plain digits (`40`).
- A tap on a shortcut writes its `shares` into the field, as the string that arrived.
- Typing changes the field and nothing else.

Both then show what the field holds. A shortcut is selected while the field holds exactly its `shares`. The thumb stands on a step, with the track filled up to it, while the field holds exactly the digits of that step. With any other text (an empty field, `007`, a number beyond the slider) the thumb stays where it is and is drawn as an outline (`--card` fill, 2 px `--border-strong`), and no part of the track is filled: the slider claims nothing about a number it cannot show. A press or an arrow key that leaves the outlined thumb on the step it rests on writes that step into the field.

**Decision:** those are tests of two strings for equality. The frontend does not read what was typed as a number and measures it against nothing: not the ends of the slider, not the stake that is left, not the balance. Whether a number of shares can be bought is the server's answer to the quote (§13).

**Decision:** the range of the slider is the server's, per outcome, and as of the answer that brought it. A price that moved since can make its top a number the server refuses. The refusal says so, and the market is read again (§13).

The stake-left line belongs to the field as its help text and the refusal as its error. While the viewer cannot trade (`viewer.can_trade` is false) or a trade of the ticket waits for its answer, the whole entry is disabled. §15 gives the names a screen reader hears.

### Quote panel

A `--background` inset block inside the sheet, `--radius-md`, with label and value rows. It reads from the top as the order does: so many shares, at this price each, for this many tokens.

| Label | Value | Shown |
| --- | --- | --- |
| Shares | `shares` | Buying and selling |
| Price per share | (bean) `average_price` | Buying and selling |
| You pay (buying) / You receive (selling) | (bean) `amount_cr` | Buying and selling |
| Pays if {outcome} wins | (bean) `payout_cr` | Buying |
| New probability | `p_yes_after` YES | Buying and selling |

Labels are `body-sm` `--muted-foreground`; values are `number`, right aligned. Every amount of tokens has its token icon, when selling as when buying. The price per share is the average of the order as the server rounded it. "You pay" and "You receive" are exact, so each can differ in its last places from the shares times the price shown. While a quote is loading, the values are skeleton bars and the labels stay put, so the sheet does not jump. With nothing to quote (no number of shares, or one the server refused) the rows keep their labels and show no value, an en dash each (§10), and nothing pulses. No line stands under the panel: the row "Pays if {outcome} wins" says what a share pays.

### Bottom sheet and dialog (shadcn `drawer`, `dialog`, `alert-dialog`)

- **Bottom sheet:** `--card`, top corners `--radius-xl`, a 36 x 4 px `--border-strong` handle centred at its top, `--scrim` behind. Maximum height 90% of the viewport. The content scrolls inside while the action button stays pinned at the bottom above the safe area, under a 1 px `--border` line that shows where the scrolling part ends. From 768 px it is a dialog.
- **Dialog:** `--card`, `--radius-xl`, `--scrim` behind, centred, 420 px wide and never wider than the viewport less 16 px on each side, at most 90% of the viewport tall, with the same scrolling content and pinned buttons. The welcome dialog and the dialogs of the admin area are dialogs at every width.
- **Close cross:** every sheet and every dialog has one at its top right: a 24 px cross in `--foreground` inside a 44 x 44 px target that stands 4 px from the top and from the right edge, with the `aria-label` "Close". It is there in addition to the other ways out, never in place of one: dragging a sheet down, a tap on the scrim, the Escape key, and a "Done" or "Cancel" button where the surface has one.
- Nothing lies under the cross and nothing scrolls under it. Every sheet and every dialog has a header row for the cross, which does not scroll. A title in that row ends 48 px before the right edge. Where the title is for screen readers only, the row stands all the same, as tall as the cross: it holds one line that says what the surface is about (the question on the trade sheet), which ends 48 px before the right edge too, or nothing.
- **A surface that reports a finished action** has the cross as well: an issued voucher, the welcome dialog. There the cross does what "Done" does. The trade sheet reports a finished trade without such a surface: its receipt is a banner over the form (§13), and the cross closes the sheet as it always does.
- **Confirmation dialog** (`alert-dialog`): the dialog of an admin action that is destructive or cannot be taken back. Its cross does what "Cancel" does. It is the one surface that a tap on the scrim does not close, so that such an action is neither confirmed nor dropped by a stray tap.
- **While a request is on its way.** A surface that has to show what became of a request it sent (a trade, issuing a voucher, redeeming a code, an admin action) cannot be closed for as long as that request is out: no drag, no tap on the scrim and no Escape closes it. For that time its cross stays in its place, disabled in `--subtle` like its other buttons. It is never taken away. A sheet that the address opened (the trade sheet) also holds against the phone's Back: a step back while its request is out puts the sheet's address back, and the sheet stays.
- The sticky trade panel of §6 is neither a sheet nor a dialog. It is part of the page, there is nothing to close, and it has no cross.

### Filter chips

A group of single-choice chips. Each chip is 44 px tall, `--radius-sm`, with `body-sm` text. Default: 1 px `--border-strong`, `--muted-foreground` label. Selected: `--raised` fill, `--foreground` label, `aria-pressed="true"`. Chips are laid out in one of two ways:

- **As a strip** (the status filter of Markets): each chip as wide as its label with 12 px on either side, 8 px apart, in one row that scrolls horizontally when it is wider than the screen. Nothing else stands in that row.
- **As a grid** (the sort of the Leaderboard below 768 px): equal columns across the content width, 8 px apart, each label centred in its chip. A grid never scrolls and never runs under another control (§13).

A chip that states a filter which is set carries a close cross and removes that filter when tapped. Such chips wrap onto as many lines as they need. One is never wider than the content column: a label that is longer is cut with an ellipsis, and the whole of it is the chip's name for a screen reader.

### Tabs and the segmented control (shadcn `tabs`, `toggle-group`)

- **Tabs** switch between the views of one screen (Portfolio: Positions and History; Admin: Markets, Codes, Risk, Users). A row 48 px tall with `label` text. The active tab is `--foreground` with a 2 px `--foreground` line under it; the others are `--muted-foreground`. A row that is wider than the screen scrolls horizontally and keeps the active tab in sight.
- **The segmented control** switches one form between two modes (Buy and Sell; "Add" and "Remove" in an adjustment of tokens). 48 px tall, `rounded-full`, a `--background` track. The selected segment is a `--foreground` fill with a `--primary-foreground` label; the other has a `--muted-foreground` label.

### Form fields (shadcn `input`, `textarea`, `label`, `native-select`)

- Label above the field (`caption`, `--muted-foreground`). Field 48 px tall, `--background` fill, 1 px `--border-strong`, `--radius-md`, 16 px text. Placeholder in `--muted-foreground`.
- Focus: the border becomes `--ring` and 2 px.
- Error: the border becomes `--no`, with the message below in `body-sm` `--no-text` next to an alert icon, linked by `aria-describedby`. A refused field that has the focus keeps the colour of the error: its border is `--no` and 2 px, for every kind of field (text, several lines, select, date and time, amount, code, shares).
- Help text and character limits sit below the field in `caption`.
- The code fields use the code style (§10), `autocapitalize="characters"`, `autocorrect="off"`, and insert the dashes as the user types.
- Degree level and programme are chosen with the picker (below), wherever they are chosen. A short fixed list of the admin area (a role, a status, a tier) is a native `select` styled like a field. A field that depends on another is disabled until the other is set.
- A number the user types (a number of shares, an amount of tokens, a bound of a filter) goes into a text field that opens the decimal keypad, or the numeric one for a whole number. The field keeps the characters a figure can have and sends them as they stand (§10).
- A date and time is entered in the browser's own field for it. While it holds no value it shows what the browser puts there (the format of a date and a time in Chrome, a date and a time of its choosing in Safari) in `--muted-foreground`, as a placeholder; once it holds a whole moment its text is `--foreground`. A field that is filled in part holds no value yet and stays in the placeholder colour.
- **Checkbox** (the shadcn checkbox): a 20 px box, `--radius-sm`, 1 px `--border-strong` on `--background`; ticked, a `--foreground` fill with a `--primary-foreground` check. The box and its text form one row at least 44 px tall, and a tap on the text ticks the box. A link inside the text is a target of its own: it opens what it names and leaves the box as it is. An error stands below the row, as under a field.

### Picker

A choice from a list that is too long to scroll through: the field shows the choice, and typing narrows the list. Degree level and programme are chosen with it in three places: the profile set-up, the Profile screen and the filter sheet of the Leaderboard (§13). It is built from the form field, the bottom sheet and a list of its own; no combobox package is added. The component is `PickerField` of `components/form`.

At every width:

- The field shows the chosen label, or its placeholder in `--muted-foreground`. Help text and an error stand under it as under every field.
- **Matching:** the query and each label are compared in lower case with diacritics folded. The query is split at whitespace, and an option matches when every part occurs in its label: "msc data" finds "MSc Data Science". Options keep the order they were given; nothing is ranked. An empty query lists every option.
- A value that is set and is not among the options (a filter the address brought) is shown as its own text and listed first, as chosen.
- In a filter the first option stands for no filter ("Any programme") and has the empty value.
- Choosing an option sets the field, closes the list and returns the focus to the field. The picker posts nothing by itself (§14).
- After the query changed, a visually hidden polite region says "{n} results", "1 result", or the sentence for no match.
- With no match the list shows that sentence in `body-sm` `--muted-foreground`.
- A picker that is disabled opens nothing.

**From 768 px: a combobox in place.**

- The field is the text input: `role="combobox"`, `aria-autocomplete="list"`, `aria-expanded`, `aria-controls` and `aria-activedescendant`, named by its label ("Level", "Programme"). Focus selects its text, and typing filters.
- The list is a `role="listbox"` named by the label, under the field and as wide as it, at most 320 px tall, scrolling inside: `--card`, 1 px `--border-strong`, `--radius-md`. An option is 44 px tall and wraps its label; the chosen one carries the check icon and `aria-selected="true"`; the active one has the `--raised` fill. The list lies over what follows it, so the form does not move; inside a sheet it is brought into view within the part that scrolls.
- Keys: Arrow Down and Arrow Up open the list and move the active option, stopping at its ends. Home and End take it to the first and the last. Enter chooses the active option, or the first match while none is active and the query is not empty. Escape closes the list and puts the chosen label back, and the surface around it stays open. Tab closes the list, puts the chosen label back and moves on, choosing nothing. A click outside does what Escape does.

**Below 768 px: a sheet.**

- The field is a button that looks like a field: 48 px tall, a chevron at its right, `aria-haspopup="dialog"`, named "{label}: {chosen label}" or "{label}: {placeholder}".
- A tap opens a bottom sheet (above) titled by the label, with the close cross. Under the title, pinned: a search field (`type="search"`, `enterKeyHint="search"`, `autoCapitalize="none"`, `autoCorrect="off"`) with a name of its own ("Search programmes"). Under it the list fills the sheet and scrolls, options 48 px tall. The sheet takes the focus to itself, as every bottom sheet does; a tap in the search field brings the keyboard, and the list stays above it.
- A tap on an option chooses it and closes the sheet. Enter on the keyboard chooses the first match.
- Inside another sheet (the filter sheet) the picker's sheet is nested: closing it leaves the sheet under it open.
- Crossing 768 px while the list is open closes it and keeps the choice.

| Picker | Placeholder | Name of the search field | No match | First option in a filter |
| --- | --- | --- | --- | --- |
| Level | "Choose a level" | "Search levels" | "No level matches." | "Any level" |
| Programme | "Choose a programme" | "Search programmes" | "No programme matches." | "Any programme" |

A programme picker is disabled until a level is chosen, and changing the level clears the programme. The longest label of the catalogue (70 characters) wraps inside its option, and nothing lies over anything else.

### List rows

56 px minimum height, 1 px `--border` divider, no outline of their own.

| Row | Left | Right |
| --- | --- | --- |
| Ledger | `description`, and `created_at` below in `caption` | Signed `amount_cr` |
| Trade | `market_question` (one line), and "Bought YES" or "Sold NO" with the date below | `amount_cr`, and `shares` below |
| Position | The question, and holdings below ("183.29 YES shares") | `value_cr`, with a status badge when settled |
| Product | `name` | The token icon and `price_cr`; the whole row is disabled when `affordable` is false |
| Voucher | `product_name`, and the expiry below in `caption` | "Show QR", or what became of it: "Redeemed", "Expired, refunded" or "Expired, refund on its way" (§13) |
| Leaderboard | `rank` in a column at least 32 px wide, then `username` on one line with `degree_programme` below in `caption` | The figure of the measure the list is sorted by. It flashes when it changes by itself (§7) |

A ledger row expands on tap to show the full `amount_cr` string. A position row and a trade row are each one link to their market. A leaderboard row is a button that opens the statistics of its player (§13). A list that pages has `next_cursor` in its answer: when the user reaches its end, the next page is asked for with that value as `cursor`.

In every row the right side keeps its width and the left side gives way: its text wraps, or is cut with an ellipsis where a row has to stay one line, before it comes within 12 px of the figure. Nothing in a row lies over anything else.

### Tables

The tables of the admin area, and the ranking of the Leaderboard from 768 px, built on the shadcn table. A heading row 44 px tall in `caption` `--muted-foreground`, 1 px `--border` dividers between the rows, no outline. A row is at least 44 px tall in the admin area and 56 px on the Leaderboard.

- A column of text is left aligned and gives way: its text wraps, or is cut with an ellipsis where a row has to stay one line.
- A column of figures is right aligned, in `number`, as wide as its widest figure, and never shrinks.
- What is neither (a date, a badge, a button) stays on one line and keeps its width.
- A table that is wider than its column scrolls horizontally inside its own box. The page does not scroll sideways.

### QR code and scanner

- **QR code:** always `--ink` modules on a `#FAFAFF` tile with `--radius-lg`, at least 240 px and at most 288 px wide. The tile is the quiet zone a camera needs: four of the code's own modules of light on every side, counted in modules so that it holds at every width, and no other padding. The print sheet shows the same tile, 40 mm wide, on its cards (§13). A dark-themed QR does not scan reliably. The code is repeated as text below it. Showing a voucher raises the screen brightness hint ("Turn up your brightness") instead of trying to control it.
- **Scanner:** a full-width camera view with a square guide in `--foreground` corners. It reports what it read once and pauses until the screen asks for the next scan. It always has a visible typed alternative ("Type the code instead"; on the Barista screen "Type the voucher code"), and a clear message when camera permission is refused. The camera needs HTTPS (SPEC A.10). Whatever decodes the picture is served by the app itself: the Content-Security-Policy allows no remote script, wasm file or worker.

### Feedback

| Component | Based on | Use |
| --- | --- | --- |
| Toast | shadcn `sonner` | Confirmation of a finished action whose result is in view already ("Profile saved", "+{value_cr} tokens", "Proposal sent. VIS reviews it before it opens."), and the one thing the app tells by itself: a notice (§13). Bottom of the screen, above the tab bar, 4 s, with a close cross at its right end (a 44 px target, `aria-label` "Close"). `--raised` fill, white text. A toast may carry one text link (§12), "View", which leads to what the toast is about and puts the toast away |
| Banner | shadcn `alert` | A condition that lasts: a paused market, a 503. Full width inside the content column, with an icon. Neutral: `--raised`. Error: `--no-soft` with `--no-text` |
| Inline error | | Under the field or above the action button it concerns |
| Result | Bottom sheet or dialog | A finished action that leaves something to keep: an issued voucher. It stays until "Done" or the close cross |
| Receipt banner | shadcn `alert` | What became of the trade that was just made, at the top of the trade ticket, above a form that is ready for the next one (§13). It has no button and no cross: there is nothing to dismiss |
| Confirmation dialog | shadcn `alert-dialog` | Confirmation of destructive or irreversible admin actions only |
| Skeleton | shadcn `skeleton` | Loading. Shapes match the content they replace |
| Empty state | | One pixel illustration (§3), one sentence, and at most one button |

Errors are never shown only as a toast: a toast disappears, and the user must be able to read what went wrong.

### Receipt banner

`--raised`, `--radius-md`, 12 px padding, as wide as the content of the sheet. A 20 px check icon in `--foreground` at its left, and beside it two lines:

1. In `label`: "Bought 50 YES shares" or "Sold 25 YES shares". The verb follows `side`; `shares` is the whole string (§10).
2. In `body-sm`: "for", the token icon with the trade's `amount_cr` as the whole string, a middle dot, the token icon with its `average_price`, "per share". The words are `--muted-foreground`, the figures `--foreground` in Barlow. The line wraps where it does not fit.

The token icon of the amount bounces once when the banner appears (§7). The banner is neither green nor red: a trade that went through is no gain (§4). It takes the focus when it appears and is read as one sentence (§15). It stays until the next trade of the ticket is confirmed, and goes with the ticket when the sheet is closed; a later trade puts its own banner in the same place.

## 13. Screens

Each screen lists its content from top to bottom. Field names are those of the API responses. Every visitor is signed in, and everyone past the profile set-up has a profile, so no screen has a signed-out state.

| Screen | Reads | Writes |
| --- | --- | --- |
| Start-up | `GET /config`, `GET /me` | |
| Profile set-up, Profile | | `PUT /me/profile` |
| Usage policy | | |
| Markets | `GET /markets` | |
| Every screen inside the app shell | `GET /stream`, the app's one event stream (§14); `GET /me`, with its `notices` | `POST /me/notices/{notice_id}/seen` |
| Market detail | `GET /markets/{market_id}`, `GET /markets/{market_id}/history` | |
| Trade sheet | `GET /markets/{market_id}/quote` | `POST /markets/{market_id}/trades` |
| Portfolio | `GET /me/positions`, `GET /me/trades` | |
| Wallet | `GET /me`, `GET /me/vouchers`, `GET /me/ledger`, `GET /vouchers/products` | `POST /vouchers`, `POST /vouchers/{voucher_id}/code`, `POST /codes/redeem` |
| Leaderboard | `GET /leaderboard` | |
| Propose market | `GET /me/proposals` | `POST /markets` |
| Admin | `GET /markets`, `GET /markets/{market_id}`, `GET /admin/code-batches`, `GET /admin/code-batches/{batch_id}/codes`, `GET /admin/risk`, `GET /admin/users` | The seven market actions and the four user actions listed there, `POST /admin/code-batches`, `POST /admin/code-batches/{batch_id}/revoke`, `POST /admin/alerts/{alert_id}/ack` |
| Barista | | `POST /barista/vouchers/redeem` |

No screen calls `GET /health` or `POST /admin/grants/monthly`. No screen opens a stream of its own: a screen says which topic it shows, and the app's one stream follows what the screens on it name (§14).

**Notices.** `notices` of `GET /me` lists what the user is told once, oldest first. The one kind is an approval: VIS opened a market the user proposed. Each notice shows as a toast (§12), "Your market is open: {question}", with the text link "View", which leads to the market. It appears by itself on whatever screen is open, because `/me` is read again whenever something of the caller's own changed (§14). The notice is then marked seen with `POST /me/notices/{notice_id}/seen`, after which `/me` lists it no more. A page tells a notice once, also when marking it seen got no answer (§14).

### Start-up (every route)

What stands in front of the app until it knows who is looking. The portal has signed the visitor in before the page loads; start-up finds out whether that person has a profile.

1. The boot screen: the top bar with the logo and nothing on its right, skeleton blocks in the content column, no tab bar.
2. The app asks `/config` and `/me` together. The answer of `/me` decides what takes the place of the boot screen:

   | `/me` answers | What is shown |
   | --- | --- |
   | The profile | The route that was opened, inside the app shell |
   | `PROFILE_REQUIRED` | Profile set-up, in place of the route. On `/policy` the usage policy is shown, so that it can be read before it is accepted |
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
   - **Level** (`degree_level`): a picker (§12) of the levels in `/config` `degrees`, each shown by its `name` and sent as its `level`, with the placeholder "Choose a level".
   - **Programme** (`degree_programme`): a picker of the `programmes` of the chosen level, with the placeholder "Choose a programme", disabled until a level is chosen. Changing the level clears it.
4. One sentence in `body-sm` `--muted-foreground`: "Other players can see your username, your degree, your tokens and your trading record."
5. A checkbox (§12), not ticked: "I accept the usage policy". The words "usage policy" are a link to `/policy`. It opens in a tab of its own, so the fields keep what was typed. This is a line of the form, not a banner and not a popup.
6. A primary "Create profile" button, disabled until all three fields are filled and the box is ticked. It sends `PUT /me/profile` with the three fields and `policy_accepted: true`.
7. A footer, below the form: the main-building dome and under it, in `body-sm` `--muted-foreground`, "Pick a name for the leaderboard. The podium remembers."

The answer is the profile, and the route that was opened takes the place of this screen. When the answer says `signup_granted: true`, the **welcome dialog** opens over that route: the 64 px token icon, "You got {balance_cr} tokens" (`h2`), "Spend them wisely, or on coffee. A share pays 1 token if you are right and nothing if you are wrong.", a primary "Start trading" and the close cross of §12, which both close it. The amount is `balance_cr` of the same answer: a profile created a moment ago holds its welcome tokens and nothing else. The bean drop plays once. A sheet the route would open by itself (the redeem sheet of a code card) waits until the dialog is closed.

States: `USERNAME_TAKEN` and a `VALIDATION_ERROR` (a username the API does not accept or keeps reserved, a programme that is not on the list) appear on the field they name, with the API's message; the other fields keep their input. A `VALIDATION_ERROR` that names `policy_accepted` stands under the checkbox. `FORBIDDEN` replaces the screen with the full-screen message. No answer or a 503 shows the banner above the button and keeps the input; saving again is safe.

### Profile (`/profile`)

An inner screen, reached from the profile button in the top bar. It changes what a profile can change: the username and the degree.

1. Top bar with a back arrow and the title "Profile".
2. A card: `username` (`h2`) and `degree_programme` in `body-sm` `--muted-foreground`; under a divider `name` and `email` with the caption "From your ETH sign-in. Only you and VIS see these." An empty `name` is left out. Under a second divider, in `body-sm` `--muted-foreground`: "You accepted the usage policy on {policy_accepted_at}.", a date and time (§10), and the link "Read the usage policy", which leads to `/policy`.
3. The three fields of the set-up screen, filled in, the same sentence about what other players can see, and a primary "Save" button that is disabled until something changed. There is no checkbox: the policy was accepted when the profile was created, and "Save" sends the three fields alone.

States as on the set-up screen. Success shows the toast "Profile saved" and stays on the screen. There is no welcome dialog here and nothing to sign out of.

### Usage policy (`/policy`)

The rules of MarkETH on one page of plain text. Whoever creates a profile accepts it, and whoever proposes a market accepts it again with that proposal: both requests carry `policy_accepted: true`. Four places link to it: the checkbox of the profile set-up, the checkbox of the propose form, the card of the Profile screen and the footer of the Wallet.

1. Top bar with a back arrow and the title "Usage policy". No tab is active. A visitor without a profile gets the frame of the boot screen in its place: the top bar with the logo and nothing on its right, no tab bar, and the title as a heading above the text. There the logo leads to the profile set-up.
2. The text below, in a column of at most 720 px. A heading is set in `h3`, a paragraph in `body` and `--foreground`, a list as a plain bulleted list. 24 px separate two sections, 8 px a heading and its text.

The page asks the API for nothing: its figures are fields of the `/config` that start-up read, written below as placeholders. A noun after a placeholder follows its number, as "tokens" does (§10). There is no illustration, no campus line and no button on the page. It is read, and left with the back arrow.

**Decision:** the policy is copy of this file, like every other sentence of the app. VIS changes it here.

The text of the page:

> **What MarkETH is**
>
> MarkETH is a prediction game for people at ETH Zurich, run by VIS. You trade YES and NO shares on questions about campus life. When a market is decided, a share of the outcome that came true pays one token and a share of the other outcome pays nothing.
>
> Tokens are free. You get {signup_grant_cr} welcome tokens when you create your profile and {monthly_grant_cr} monthly tokens every month, counted from that day. VIS also hands out codes, which you redeem for tokens in the Wallet.
>
> Nothing is sold. Tokens, codes and vouchers cannot be bought, from VIS or from anyone else, and none of them can be turned into money. No real money is involved at any point.
>
> **Markets you may propose**
>
> Anyone with a profile can propose a market. VIS reads every proposal and opens the ones that fit this policy. A market you may propose:
>
> - asks a question with two outcomes, YES and NO, that facts will decide and opinions will not;
> - says what counts as YES and what counts as NO, so that no case is left open;
> - names its source: a public page, an official announcement or a count that anyone can check;
> - has its dates: when trading closes, and by when the result is known. Trading runs for at most {max_market_lifetime_days} days, and the result has to be known at most {max_resolution_delay_days} days after trading closes.
>
> You can propose {max_proposals_per_day} markets a day.
>
> **Markets you may not propose**
>
> VIS opens no market about:
>
> - the private matters of a person who is named or can be identified: their health, their relationships, their money, their conduct;
> - anyone's grades or exam results, your own included;
> - an outcome that one trader can decide alone, or bring about by what they do;
> - party politics, elections and votes;
> - religion.
>
> VIS is a student association within VSETH and takes no side in politics or religion.
>
> **Fair play**
>
> - One account per person. Your account belongs to your ETH sign-in: do not trade through another person's account, and do not let anyone trade through yours.
> - You cannot trade a market you proposed.
> - Do not manipulate an outcome. Do not trade a market whose result you can decide or influence, and do nothing to make a market end your way.
> - You can have at most {max_stake_per_market_cr} tokens at stake in one market, so that no single trader sets a price.
> - Admins of VIS settle the markets. After trading closes, an admin resolves a market to YES or NO from its source and states the evidence, which is shown on the market's page. An admin who traded a market does not settle it.
> - An admin may void a market that cannot be decided fairly, for example when its source disappears or its question proves unclear. A voided market pays nobody: every trader gets back their net stake, which is what they paid for shares minus what they received for selling, and never less than zero.
>
> **Vouchers**
>
> Tokens can be turned into a voucher for one product at the bar: a coffee, a drink or a snack. The Wallet lists the products and the tokens each one takes.
>
> A voucher is a QR code. You show it at the bar, it is scanned, and with that it is used up.
>
> A voucher is valid for {voucher_expiry_days} days. One that is not used by then expires, and its tokens return to your balance.
>
> Vouchers are not for sale and cannot be exchanged for money.
>
> **What other players can see about you**
>
> Other players see your username, your degree programme and your record on the leaderboard: the tokens you hold, the number of your trades, your win rate and your payout ratio.
>
> They do not see your name or your email address. Both come from your ETH sign-in and are shown to you and to the admins of VIS only. They do not see which markets you trade or what you hold in them, and a market does not show who proposed it.
>
> VIS keeps a record of every movement of tokens and of every action an admin takes.
>
> **What VIS may do**
>
> VIS runs MarkETH and keeps it fair. To do that, VIS may:
>
> - reject a proposal, or change its wording and its dates before it opens. You see the reason for a rejection;
> - pause trading in a market, close it early or void it;
> - correct a balance that is wrong, with the reason on record;
> - suspend an account that breaks this policy. A suspended account is locked out of MarkETH until VIS reinstates it;
> - change this policy. This page shows the policy in force.

### Markets (`/`)

1. Top bar.
2. Filter chips (§12): "Open" (the default), "Closed", "Resolved", "All". Each sends its `status` to `GET /markets`:

   | Chip | `status` |
   | --- | --- |
   | "Open" | OPEN and HALTED, so a paused market stays in sight |
   | "Closed" | CLOSED |
   | "Resolved" | RESOLVED and VOID |
   | "All" | None: the API then lists every status but PROPOSED, REJECTED and EXPIRED |

3. Market cards, in the order the API returns them.
4. A secondary "Propose a market" button after the list, leading to `/propose`.

States: six skeleton cards while loading. Empty: the dome illustration and "No markets yet. Quieter than the HG on a Sunday." with the propose button. Error: a banner with "Try again".

The list is live: prices and volumes follow the stream, and a change of a market's status reads the list again (§14). On every card the Price and "Vol." follow `market.quote`, and the Price flashes when it changes (§7); a card changes in place, whatever the number of columns (§6). After `market.status` the list is as the API returns it: a market that opened appears, one that stopped trading shows its badge where "Yes" and "No" stood, and one outside the chosen statuses is not listed. The list is also read again when the window regains focus; a pull to refresh is the browser's own reload.

One thing no event says: that a market reached its `close_at`. The list therefore asks `GET /markets` again at the earliest `close_at` among the cards it shows with `trading_open` true, and after the answer at the earliest one that is left. The timer asks; it decides nothing. Whether a card keeps its "Yes" and "No" buttons is `trading_open` of the answer (§14).

### Market detail (`/markets/[id]`)

The answer of `GET /markets/{market_id}` always carries its `viewer` block, because whoever reads a market has a profile. No part of this screen has a signed-out form, and nothing on it says how the market is priced: no tier, no liquidity figure, no loss bound (§10).

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
| RESOLVED | The Price is replaced by the outcome ("YES" in `--yes`, or "NO" in `--no`, `display` size). In **Your position**, "Paid out" with `payout_cr` takes the place of "Your stake". When the market resolves while it is on screen (a `market.status` event brought the change) and `payout_cr` is not `0`, the bean drop plays into that figure (§7) |
| VOID | Neutral banner "This market was voided. Stakes were refunded." In **Your position**, "Refunded" with `refund_cr` takes the place of "Your stake" |
| PROPOSED, REJECTED, EXPIRED | The API shows these to their proposer and to admins only. The badge, the rules and, when rejected, `rejection_reason`; an expired proposal has no reason. No Price, chart, outcome buttons or position, and the screen asks for no history |

At most one banner shows. The market's status decides it, and `OWN_MARKET` speaks only while the market is OPEN. The other reason, `MARKET_NOT_OPEN`, has no words of its own: the row of the status has said it.

Live: the screen names its market to the app's stream from the moment it opens, whatever the market's status (§14). The Price, the chart and the volume follow `market.quote`, and the Price flashes when it changes (§7). `market.status` reads the screen again, and the bean drop plays for a payout that arrives like this. While **Your position** is shown, a `market.quote` is followed by one read of the market for the viewer's side: what the holding is worth and the sell chips are the server's to work out (§14). An open quote of the trade sheet or the panel is untouched by all of this.

No event says that `close_at` has passed. While `trading_open` is true, the screen therefore asks `GET /markets/{market_id}` again at the market's `close_at`, and its chart with it. The timer asks; it decides nothing: the badge, the banner, the outcome buttons and the deadline line all follow the answer, which brings CLOSED and `trading_open` false (§14). A proposal in review is asked about at its `close_at` in the same way: the answer brings EXPIRED.

States: skeletons in the shape of the Price, the chart and the rules while loading. A market the API does not know is the "not found" screen. Error: a banner with "Try again".

### Trade sheet (`/markets/[id]`)

Opened over Market detail from an outcome button, from "Sell", or by the address (`?buy=YES`, `?buy=NO`, `?sell=YES`, `?sell=NO`). An address that asks for a trade the viewer cannot make (`viewer.can_trade` is false, or a sell with no shortcut that is allowed) opens the market without the sheet. Below 768 px it is the bottom sheet, from 768 px a dialog, and from 1024 px the sticky panel of §6, which is in place whenever the outcome buttons are shown and starts on Buy and YES. The sheet and the dialog have the close cross of §12; the panel has nothing to close. Top to bottom:

1. The header: the question on one line in `body-sm` `--muted-foreground`, and at its right the close cross. It is the header row of §12 and does not scroll. In the panel the question stands alone.
2. The receipt banner of §12, while the ticket holds the receipt of a trade.
3. A **Buy / Sell** segmented control. "Sell" is enabled when at least one entry of `viewer.sell_presets` has `allowed: true`.
4. The outcome toggle. When selling, an outcome is offered when one of its two entries in `viewer.sell_presets` has `allowed: true`.
5. **Buying:** the shares entry of §12 for the selected outcome: the slider and the shares field side by side, under them the shortcuts, then the stake-left line. **Selling:** two chips, "Half" and "All", from the entries of `viewer.sell_presets` for the selected outcome (`portion` HALF and ALL). Each is 72 px tall and shows three lines: its name in `label`, the token icon with its `proceeds_cr` in `number`, and its `shares` in `caption` ("25 shares"). A chip is disabled when `allowed` is false. The chosen chip's `shares` string is sent as it arrived.
6. The quote panel.
7. A line with the balance: "Balance" and the token icon with `viewer.balance_cr`.
8. The Confirm button, 56 px. Its fill follows the outcome when buying ("Buy YES" on `--yes`, "Buy NO" on `--no`) and is `primary` white when selling ("Sell YES shares", "Sell NO shares").

The three frames hold the same blocks in the same order. The sheet holds 343 px of content on a 375 px phone and the dialog 386 px; the panel is 360 px wide. Where the ticket is taller than 90% of the viewport its body scrolls and Confirm stays pinned: always on a 667 px tall phone, and on an 844 px tall phone only while the banner shows. The body of the panel scrolls under the question when the window is short. Nothing lies over anything else and the page does not scroll sideways.

An order is a number of shares on both sides. A share pays one token if its outcome comes true, so the sheet states an order as a product: so many shares at this price each. Buying, the number is the user's to set; the server says per outcome where the slider runs and which shortcuts can be taken (`viewer.buy`). Selling follows SPEC A.11: the server works out half and all of each holding, what each would bring in and whether each sale is allowed, so the frontend halves, prices and compares nothing. There is no free-entry field for a sale.

**Every amount of tokens carries the token icon**, when selling as when buying: what a sell chip brings in, the price per share, "You pay" and "You receive", what the shares pay, the balance, and the two figures of the receipt banner. A number of shares carries the word "shares" or stands beside the label "Shares", and never the icon (§10).

Behaviour:

- **The size of a buy** is what the shares field holds. The slider and the shortcuts fill it (§12), and what the user types stays as typed. The form opens with an empty field. Changing the outcome keeps the field and draws the slider and the shortcuts of the other outcome.
- **The quote** comes from `GET /markets/{market_id}/quote`, asked for `quote_debounce_ms` (from `/config`) after the last change to the number, the outcome or the side. Buying sends `side`, `outcome` and the text of the field as `shares`; selling sends `side`, `outcome` and the chip's `shares`. An empty field asks for nothing. Confirm is disabled until a quote for the current input has arrived.
- **A quote never outlives its input.** When the field is emptied or the chosen chip is dropped, the quote on screen is dropped at once. The same input set again is asked again. A trade that went through empties the input, so the quote that guards the trade after it is always one the server gave after it.
- **The server judges the number.** For a buy the quote applies the viewer's stake limit, the balance and the smallest trade, and answers what the trade itself would: `LIMIT_EXCEEDED`, `INSUFFICIENT_BALANCE`, `TRADE_TOO_SMALL`, or `VALIDATION_ERROR` for a text that is no number of shares. The API's `message` then stands under the stake-left line, the border of the field turns `--no`, the quote panel shows no values, and Confirm stays disabled until another number has been quoted. The sentence names what the shares would cost, because the panel cannot: "This costs 104.2 tokens. You can still put 73.81 tokens into this market." `LIMIT_EXCEEDED` also refreshes the stake-left line from `stake_left_cr` and `stake_limit_cr` in `details`. After `LIMIT_EXCEEDED` and `INSUFFICIENT_BALANCE` the market is read again, so the slider and the shortcuts are those of the price as it is. The frontend refuses no number by itself.
- The quote is about the viewer in every respect, so it can also be answered `MARKET_NOT_OPEN`, `INSUFFICIENT_SHARES` or `FORBIDDEN`: trading ended, the holding changed, or the viewer proposed the market. The API's `message` then stands above the button as it does for a trade, Confirm stays disabled, and the market is read again, so the sheet shows what can still be done.
- A quote that fails for another reason (no connection, a 503, `RATE_LIMITED`) shows the banner of §14 above the button with "Try again", and Confirm stays disabled.
- **While the viewer cannot trade** (`viewer.can_trade` is false: trading stopped while the ticket was open, or the panel stands beside a market its viewer proposed) the entry and Confirm are disabled and no quote is asked. The sheet has no sentence of its own for this: the banner of Market detail behind it says why.
- Confirm sends `POST /markets/{market_id}/trades` with what was quoted and the quote as its guard. Buying: `side`, `outcome`, `shares` (the text of the field), `max_cost_cr` (the quote's `amount_cr`) and `quote_version`. Selling: `side`, `outcome`, `shares`, `min_proceeds_cr` (the quote's `amount_cr`) and `quote_version`. A price that moved against the user is therefore always answered `QUOTE_STALE`.
- On Confirm the button shows its spinner and is disabled until the response arrives. A double tap cannot create two trades. The request carries an `Idempotency-Key` that belongs to that one request body; §14 says how long the two are kept and when "Try again" sends them again.
- **The sheet cannot be closed while its trade is out.** From Confirm until the answer the cross is disabled (§12), and dragging, the scrim, Escape and the phone's Back do nothing. The verdict is therefore always seen where the trade was made: the receipt banner, a refusal, or "Try again" for a trade without an answer.
- The quote panel shows the quote, not the input: "Shares" is the quote's `shares`, "You pay" its `amount_cr`.
- **Success:** the receipt banner appears at the top of the ticket, and the form under it is ready: the shares field is empty, no chip is chosen, the side and the outcome are what they were, and the balance and the stake-left line are those of the trade's answer. The part of the sheet that scrolls is brought to its top, the banner takes the focus, and its token icon bounces once (§7). Nothing is dismissed and nothing reads "Done": the sheet stays open until the user closes it. The market screen behind it updates from the response, and the market is read again at once, which brings the slider, the shortcuts and the sell chips as they are after the trade.
- **The next trade** is made on the same sheet. After a buy, "Sell" is enabled as soon as the market that was read again has a sell chip that is allowed. The next Confirm takes the banner away and builds another body with another key; the banner of that trade takes its place.
- **Selling the last shares.** When, while selling in a market the viewer may trade, the selected outcome has no chip left that is allowed, the ticket moves to the other outcome if that one can be sold, and otherwise to Buy on the same outcome.
- **`QUOTE_STALE`:** the panel updates to the quote in `details.quote`, changed values flash, and an inline notice above the button shows the API's `message` ("The price moved. Review the new quote and confirm again."). The button returns to enabled. Nothing is submitted automatically.
- **Other errors of the trade:** shown inline above the button with the API's `message`; the sheet stays open and the input is kept. `LIMIT_EXCEEDED` also refreshes the stake-left line from `stake_left_cr` and `stake_limit_cr` in `details`. A refusal that says the screen is behind (`INSUFFICIENT_BALANCE`, `INSUFFICIENT_SHARES`, `MARKET_NOT_OPEN`, `LIMIT_EXCEEDED`, `FORBIDDEN`) reads the market again, and the quote is asked for again; a quote that then fails with the code of the refusal above the button is not said a second time.
- A sheet that is closed forgets its number and its receipt. It does not forget a trade that has no answer (§14): such a trade can be put away and found again, because nothing is on its way while it waits for "Try again". A verdict that arrives while no ticket of the market is on screen is told with a toast ("Bought 50 YES shares", "Sold 25 YES shares"); the next opening finds an empty form without a banner.
- **Nothing that arrives by itself touches an open quote.** An event of the stream, a poll and the close time read the market, and with it the slider, the shortcuts and the sell chips; the quote on screen stays what the server gave for the input (§14).

No sentence of the ticket is a joke, and none names a tier, a liquidity figure or a loss bound (§10).

### Portfolio (`/portfolio`)

1. Top bar.
2. A summary card: "Value of open positions" with `total_value_cr` in `number-lg`.
3. Tabs: **Positions** and **History**.
4. Positions: position rows from `GET /me/positions` in API order, under the subheadings "Open" and "Settled"; a market is settled when its `status` is RESOLVED or VOID. A row names each holding whose `shares` is not `0` ("183.29 YES shares"), or "No shares held" when there is none. A settled row shows "Paid out" or "Refunded" with the token icon and `payout_cr` or `refund_cr` in place of `value_cr`. A row opens the market.
5. History: trade rows from `GET /me/trades`, loading more with `next_cursor` as the user scrolls. A row opens its market.

Empty: the empty-cup illustration and "No positions yet. Your cup is empty." with a primary "Browse markets" button. An empty History says "No trades yet." Loading: a skeleton summary card and five skeleton rows. Error: a banner with "Try again".

"Paid out" and "Refunded" of a market that settles while the screen is open show without a reload: a settlement changes the caller's own (§14). The value of open positions is as read when the screen opened or the window got the focus back.

### Wallet (`/wallet`)

1. Top bar.
2. Balance card: the 32 px token icon and `balance_cr` in `display`, labelled "Your tokens", and below it "Next monthly tokens: {monthly_grant_cr} on {next_monthly_grant_at}", a date and time (§10). Monthly tokens come due every month on the day and at the time of day the profile was created.
3. Two buttons side by side: primary "Get a voucher", secondary "Redeem code".
4. **Vouchers** from `GET /me/vouchers`, as voucher rows (§12) in the API's order. A voucher whose `status` is ACTIVE shows "Valid until" with `expires_at` and a "Show QR" button. The others are muted. A REDEEMED voucher shows "Redeemed" with `redeemed_at`. An EXPIRED voucher says whether its tokens are back: "Expired, refunded" when `refunded` is true, and "Expired, refund on its way" while it is false, which is the short time between the expiry and the booking of the refund (§14). The screen reads this from `refunded` and looks at no balance for it. With none, one line: "No vouchers yet."
5. **Activity:** ledger rows from `GET /me/ledger`, paged with `next_cursor`.
6. A footer at the foot of the screen, in `body-sm` `--muted-foreground`: the link "Usage policy", which leads to `/policy`.

Flows:

- **Redeem code** opens a sheet (§12, with its close cross) that holds the code field, a "Scan QR" button and a primary "Redeem", which sends the code alone to `POST /codes/redeem`. Success closes the sheet with the toast "+{value_cr} tokens", sets the balance to the answer's `balance_cr` and plays the bean drop. A failure shows the API's `message` under the field and nothing of ours. For a code that cannot be redeemed that is always the same sentence ("This code is not valid."), and the screen never hints at why (SPEC §2).
- **A code card** carries a link, `/wallet?code=...`. The Wallet reads `code` from the address when it opens, removes it from the address at once (replacing the history entry, so that no address, back step or bookmark keeps it) and opens the redeem sheet with the field filled in. The user still taps "Redeem". "Scan QR" inside the sheet reads the same cards: it takes the `code` out of a link, or takes what it read as it is when that is no link, and fills the field. A link without a `code` fills nothing, and the sheet says "No code in this QR." A link is never sent to the API.
- **Get a voucher** opens a sheet with two steps. The close cross is there in both, and on the voucher that follows.
  1. The products from `GET /vouchers/products`, in the API's order, as product rows (§12). A product with `affordable: false` is disabled and stays in the list.
  2. After a tap on a product: "Turn {price_cr} tokens into a voucher for {name}?" (the word follows the string, §10), the line "Valid for {voucher_expiry_days} days. An unused voucher is refunded." with the days from `/config`, a primary "Get voucher" and a ghost "Back". "Get voucher" sends the product's `id` as `product_id` to `POST /vouchers`.

  The answer is shown as the voucher: the `product_name` (`h2`), the QR tile holding `code`, the code as text, "Valid until" with `expires_at`, the steaming cup with "Show this at the bar. You earned it, sort of.", the brightness hint and a "Done" button. "Done" and the close cross both put the voucher away. The balance becomes the answer's `balance_cr`, and the voucher is in the list behind the sheet.
- **Show QR** asks `POST /vouchers/{voucher_id}/code` for a fresh code and shows the same voucher view, close cross included, with the answer's `product_name`, `code` and `expires_at`. Every tap gives the voucher another code, and a code shown before stops working. An answer of `CONFLICT` (the voucher is used up or has expired) or `NOT_FOUND` shows the API's `message` in the sheet and refetches the vouchers.
- **A voucher code lives on screen only.** The API sends it once, in the answer that issued the voucher or gave it a fresh code. The Wallet holds it in memory while the voucher view is open and writes it to no browser storage. Closing the view forgets it, and "Show QR" brings another.
- "Get a voucher" is enabled when `can_get_voucher` in `GET /me` is true. When it is false the button is disabled, with a line under it: "You do not have enough tokens for a voucher yet." The frontend looks at neither the balance nor at what a product takes; `can_get_voucher` and `affordable` are the server's answers (SPEC A.11).
- **An issue without an answer.** When `POST /vouchers` gets no answer (§14), the sheet closes, a neutral banner reads "We did not get an answer. If your voucher was issued, it is in the list below.", and the Wallet refetches `GET /me/vouchers` and `GET /me`. "Get a voucher" stays disabled until both have answered. A voucher that is in the list was issued, and "Show QR" gives it a code.
- The product list is a list, not a shop: no images, no basket, no quantities (§2, principle 7).

States: skeletons for the balance card, two voucher rows and five ledger rows while loading. Error: a banner with "Try again". An Activity without rows shows the empty cup and "Nothing here yet."

The balance card, the vouchers and the Activity follow what is booked while the screen is open (§14): a payout shows as a ledger row without a reload.

### Leaderboard (`/leaderboard`)

Modelled on Polymarket's leaderboard, with the top three turned into a podium. Every player with a profile is on it. The server ranks, sorts and filters; the screen sends the choice and shows the answer in the order it arrives (golden rule 6).

The screen is one column at every width: 343 px of content on a 375 px phone, and from 768 px the 720 px column of §6, which holds 688 px of content and stays that wide at 1024 px and beyond. A ranking is a list and gains nothing from more width. Nothing on this screen lies over anything else at 375 px, at 768 px or at 1024 px: where two things compete for room, the text below says which one gives way. Top to bottom:

1. Top bar.
2. **Title row**, 44 px tall: the title "Leaderboard" (`h1`) at the left, and at the right two buttons 8 px apart.
   - The order button: a 44 px square secondary button with an arrow icon. It flips `order` between "Highest first" (`desc`) and "Lowest first" (`asc`), and its `aria-label` names the active one.
   - "Filter": a secondary button, 44 px tall, with a count badge while filters are set. It opens the filter sheet.
   - The title is never cut and the buttons never lie over it. Where the three do not fit on one line (a 320 px phone while filters are set) the two buttons move to a line of their own under the title, at the left.
3. **Measure line**, 4 px under the title row: one `caption` line in `--muted-foreground` that states the measure the list is sorted by. It wraps onto as many lines as it needs.

   | `sort` | Chip | Figure | Measure line |
   | --- | --- | --- | --- |
   | `credits` | "Tokens" | `credits_cr`, which the API makes of `balance_cr` and `positions_value_cr` | "Tokens in the wallet plus the value of open positions." |
   | `win_rate` | "Win rate" | `win_rate` | "Share of resolved markets that returned more than was put in." |
   | `trades` | "Trades" | `trades` | "Number of trades made." |
   | `avg_payout_ratio` | "Payout" | `avg_payout_ratio` | "Tokens returned per token put in, over resolved markets." |

4. **Sort chips**, 16 px under the measure line: the four chips of the table (filter chips, §12), one of them selected. Below 768 px they form a grid of four equal columns across the content width, 8 px apart, which makes each 79 px wide at 375 px; on a screen under 344 px wide the grid is two by two. From 768 px each chip is as wide as its label and the four stand at the left. Nothing shares their row, and they never scroll. The list opens on "Tokens", "Highest first".
5. **Set filters**, 12 px under the chips and only while filters are set: one removable chip (§12) for each ("BSc Computer Science", "Win rate from 50%").
6. **Podium**, 16 px below, in a "Highest first" list.
7. **Ranking**, 16 px under the podium: every row that is not on the podium. More rows load with `next_cursor` as the user scrolls.
8. **Your row**, pinned at the foot of the screen while the viewer's own row is out of sight.

The order of these eight is the same at every width. What changes with the width:

| Part | 375 px | 768 px | 1024 px |
| --- | --- | --- | --- |
| Content width | 343 px | 688 px | 688 px |
| Title row | 44 px tall; title, order button and "Filter" on one line | The same | The same |
| Sort chips | Four equal columns of 79 px, 44 px tall | As wide as their labels, 44 px tall | The same |
| Podium column | 97 px | 212 px | 212 px |
| Trophies | 96 px and 72 px | 96 px and 72 px | 96 px and 72 px |
| Username on the podium | `label` | `h3` | `h3` |
| Figure on the podium | `number` for all three | `number-lg` for first place, `number` for the others | The same |
| Ranking | Rows of at least 56 px | A table with rows of 56 px | The same |
| Your row | A bar 73 px tall, above the tab bar | A bar 73 px tall, at the lower edge of the window | The same |
| Filter sheet | Bottom sheet | Dialog, 420 px wide | The same |

**Podium.** One `--card` with 16 px of padding at its top and sides and none at its foot. Inside it three columns of equal width, 8 px apart, for the rows of `rank` 1, 2 and 3: second place at the left, first in the middle, third at the right. A screen reader hears them as first, second, third. The columns are aligned at their lower edge, so the three steps stand on the lower edge of the card and first place stands highest. Each column is centred and holds, top to bottom, 4 px apart:

1. The trophy: 96 px for first place, 72 px for the others, and never wider than its column.
2. The rank as text ("1st", "2nd", "3rd") in `caption` `--muted-foreground`.
3. The `username` on one line. A name wider than the column is cut with an ellipsis. The whole name stays in the title attribute and is what a screen reader hears.
4. "You" in `caption` `--muted-foreground`, in the column of the row the API marks `is_viewer: true`.
5. The figure of the sorted measure, on a line of its own. It is never cut and never leaves its column: a figure wider than its column (an amount of a million tokens on a phone) wraps inside it, the amount going under its token icon.
6. The step, 8 px lower: a flat `--raised` block as wide as the column, with square corners, 48 px tall for first place, 32 px for second and 20 px for third. Pixel-art blocks, not rounded cards.

No text of a column is wider than the column, so a long name or a large figure never reaches into the next one.

How many players the list holds decides what the podium shows:

| The list holds | Podium | Under it |
| --- | --- | --- |
| Three players or more with a figure | All three places | The rows from `rank` 4 on, when there are any |
| Two players with a figure | First and second place; third is an empty place | Nothing, or the players without a figure |
| One player with a figure | First place, in the middle; second and third are empty places | Nothing, or the players without a figure |
| Players, but none with a figure for the sorted measure | Three empty places, under the line "Nobody here has a resolved market yet. The podium is yours to take." | Every player, as rows |
| Nobody, because of the filters | No podium | "Nobody matches these filters." with a secondary "Clear all" |

An **empty place** is the outline of its step and nothing else: a 1 px `--subtle` line along the top and the sides of the block, as tall as the step of that place, with no trophy, no text and no figure. The places that are filled keep their column and its width: a podium of one is the middle column between two outlines. With no row under the podium there is no list and no table heading under it.

**Ranking.** Below 768 px, leaderboard rows (§12) of at least 56 px: `rank` in a column at least 32 px wide (a rank of five digits widens it), 12 px of space, then the `username` on one line (cut with an ellipsis, with "You" beside it on the viewer's row) and under it `degree_programme` in `caption` on up to two lines, cut with an ellipsis after the second; at the right the figure, which keeps its width and stays at least 12 px from the text. From 768 px the rows are a table (§12) with all four measures as columns: "Rank" (56 px wide), "Player" (the `username` and under it `degree_programme`, each on one line and cut with an ellipsis; this column takes what the others leave), then "Tokens", "Win rate", "Trades" and "Payout", right aligned, each as wide as its widest figure and 16 px from the next. A tap on the heading of a measure sorts by it, a second tap flips the order, and the heading carries `aria-sort`.

**Your row.** The row the API marks with `is_viewer: true` has a `--raised` background and the label "You"; on the podium the label stands under the username. The `viewer` row of the response is the viewer's place under the same sort and filters, wherever it ranks. It is pinned whenever the viewer's row in the list is not on screen: not loaded, scrolled away, or on a podium that is out of sight. When `viewer` is null the filters leave the viewer out and nothing is pinned.

- The pinned row stands in a bar across the whole width of the window: directly above the tab bar below 768 px, and at the lower edge of the window from 768 px, where the tabs are in the top bar. The bar is `--card` with a 1 px `--border` line on top and 8 px of padding above and below the row: 73 px tall, plus the safe area where no tab bar lies under it.
- Inside the bar, in the content column, stands the viewer's row: 56 px tall, `--raised`, `--radius-md`, with `degree_programme` on one line. A pinned row that is one of the three podium rows carries its 24 px trophy between the rank and the username.
- A list the viewer is in keeps as much room at the foot of the content column as the bar is tall, and 16 px more, whether or not the bar shows: room that came and went with the bar would leave the last row under it in the moment the bar appears. Scrolled to its end, the list shows its last row, or the skeleton of a page that is loading, whole and 16 px above the bar. Rows pass under the bar while the list scrolls, as they pass under the tab bar; the bar never rests on one.

**Player statistics.** A row of the ranking, a filled place of the podium and the pinned row are buttons: each opens the statistics of its player, in a bottom sheet below 768 px and a dialog from 768 px, with the close cross (§12). From 768 px the cell of the username holds the row's button, and a tap anywhere on the row opens it. On the podium the three are reached in the order first, second, third. The title of the sheet is the `username`, and under it stands one line: "You" on the viewer's own row, the level by its `name` from `/config`, and the programme as sent. Then one list of labelled figures:

| Label | Figure |
| --- | --- |
| "Rank in this list" | `rank`: the place in the list as it is shown, under its sort and its filters |
| "Tokens" | `credits_cr` |
| "In the wallet", indented under it | `balance_cr` |
| "In open positions", indented under it | `positions_value_cr` |
| "Win rate" | `win_rate` |
| "Payout" | `avg_payout_ratio` |
| "Trades" | `trades` |
| "Resolved markets" | `settled_markets` |

The three amounts stand with every digit (§10), so the two parts are seen to make the whole; a missing rate is an en dash. The sheet asks the API for nothing and works nothing out: each figure is a field of that player's row as the list holds it. An open sheet therefore follows the list when it is read again (§14), and closes if its player leaves the list.

**Filter sheet.** The bottom sheet below 768 px and the dialog from 768 px (§12), with the title "Filter" (`h2`) and the close cross in its header. The part that scrolls holds, 16 px apart:

1. A Level picker and a Programme picker (§12) from `/config` `degrees` (`degree_level`, `degree_programme`), each as wide as the sheet. The first option of each filters nothing: "Any level", "Any programme". Programme is disabled until a level is chosen. Below 768 px a picker opens its own sheet over the filter sheet, and closing that one leaves the filter sheet open.
2. One group per measure: its name in `label`, and under it a "From" and a "To" field side by side in two equal columns, 12 px apart (165 px each at 375 px, 188 px in the dialog). The group "Tokens" says what its figure is made of, in `caption` beside its name: "Wallet plus open positions".

   | Row | Sent as | Typed as |
   | --- | --- | --- |
   | "Tokens" | `min_credits_cr`, `max_credits_cr` | An amount; the string that was typed is sent |
   | "Win rate" | `min_win_rate`, `max_win_rate` | A percentage; sent as a fraction by moving the decimal point two places in the string |
   | "Trades" | `min_trades`, `max_trades` | A whole number |
   | "Payout" | `min_avg_payout_ratio`, `max_avg_payout_ratio` | A number such as 1.5 |

   A field typed in percent ends in "%" and one typed as a ratio in "×": the unit stands inside the right end of the field, and the text ends 36 px before that end, so what is typed never runs under the unit. The two fields of a group are aligned at their tops: an error under one does not move the other.

The foot is pinned under the line of §12: a primary "Show results" and under it a ghost "Clear all", each as wide as the sheet. A field that has the focus is scrolled clear of the foot and of the keyboard.

Rules:

- Sort, order and every filter are query parameters of `GET /leaderboard` and live in the address under the same names (`/leaderboard?sort=win_rate&order=asc&degree_level=MASTER`), so a view can be shared and the back button works. The screen sends no `limit`: the page size is the API's. The frontend never reorders, filters, ranks or totals rows.
- Usernames, ranks and figures are shown as the API sends them. A real name or an email address never appears here.
- **Decision:** a podium place needs a figure. A place is empty when the list has no row of that `rank`, or when that row has no figure for the sorted measure; such a row is listed under the podium with the others. A "Lowest first" list has no podium and shows no trophy: a trophy is not for last place.
- A player with no resolved market has no win rate and no payout ratio: the figure is an en dash. The server sorts those players last in both directions and leaves them out as soon as a filter on either rate is set.
- A filter value the API rejects (`VALIDATION_ERROR`) is marked on its field in the sheet; the list keeps its last result. The frontend does not compare a "from" with a "to" itself.
- **The ranking follows the stream:** when it changed, the rows on screen are read again, at most every few seconds (§14). Every row the list has loaded, the podium and the pinned row are shown in the order the API returns them. A figure that changed flashes (§7). A row that changed its place stands in its place: nothing slides, and the page keeps its scroll position.
- Each page is ranked when it is asked for, so a player can show on two pages or on none while balances move. The screen shows what it gets, and the next reading puts it right.
- The bean drop (§7) plays once on the first-place figure when the viewer opens the unfiltered "Tokens" list while `viewer.rank` is 1. No other animation, no confetti.
- Without filters the list always holds at least the viewer. Loading: a skeleton podium (the card, with blocks where the three places will be) and five skeleton rows. Error: a banner with "Try again".

### Propose market (`/propose`)

An inner screen with the title "Propose a market". A single-column form, one field per row, in this order: "Question" (`question`), "YES means" (`yes_definition`), "NO means" (`no_definition`), "How it is decided" (`resolution_criteria`), "Source" (`resolution_source`), "Trading closes" (`close_at`), "Decided by" (`resolve_by`). The form asks nothing about how the market is priced: VIS sets that when it approves (§10).

- Each text field shows its character limit from `/config`: `question_min_length` to `question_max_length` for the question, `short_text_max_length` for the two definitions and the source, `long_text_max_length` for how it is decided.
- The two times are entered and shown in the `/config` `timezone` and sent as ISO 8601 with their offset. Their limits (`max_market_lifetime_days`, `max_resolution_delay_days`) are stated in help text from `/config`.
- Under the fields, two notes in `body-sm` `--muted-foreground`: "You cannot trade a market you propose." and "You can propose {max_proposals_per_day} markets a day."
- Then a checkbox (§12), not ticked: "I accept the usage policy". The words "usage policy" are a link to `/policy`. It opens in a tab of its own, so the form keeps what was typed. What a market may and may not be about is said there, and nowhere on this form. The policy is accepted anew with every proposal: the box is empty whenever the form is.
- Primary "Submit for review", disabled until every field is filled and the box is ticked. It sends `POST /markets` with the seven fields and `policy_accepted: true`. Validation errors from the API are placed on their fields using `details`, one that names `policy_accepted` under the checkbox; `LIMIT_EXCEEDED` shows the API's `message` above the button.
- **Success** shows the toast "Proposal sent. VIS reviews it before it opens." (§12). There is no dialog: the form is empty again, its box unticked, and the proposal stands at the top of the list below, where its status shows.
- Below the form, **My proposals** from `GET /me/proposals`: the question, a status badge and, when rejected, `rejection_reason`, and nothing else about the market. A proposal that was not decided before its `close_at` reads "Expired", with the line "It was not reviewed before its closing time." The badge of a proposal that was resolved names its outcome ("Resolved YES", "Resolved NO") from `outcome` of the row. A proposal whose `status` is none of PROPOSED, REJECTED and EXPIRED is a link to its market. With none: the blackboard illustration and "No proposals yet."
- A row in review turns at its `close_at`, and no event says so: the list asks `GET /me/proposals` again at the earliest `close_at` among its rows in review (§14). When VIS opens a proposal, its proposer is told by a notice (above), on whatever screen is open.

### Admin (`/admin`, `/admin/markets/[id]`, `/admin/codes`, `/admin/risk`, `/admin/users`)

For role ADMIN; anyone else sees the "not found" screen. Four tabs under the top bar, each a link to its route: **Markets**, **Codes**, **Risk**, **Users**. Beside them a "Scan vouchers" link leads to the Barista screen. Designed for 1024 px; usable on a phone with horizontal scrolling tables. Density is higher here: rows are 44 px and `body-sm`, and a row with two lines in a cell is as tall as those need. Every dialog of the admin area has the close cross of §12.

- **Markets (`/admin`):** a "Proposals" queue first, from `GET /markets` with `status` PROPOSED, then all markets with a status select, labelled "Status", that has one option per status, "Expired" among them. Each choice of the select sends its one `status`; "All" sends all eight, because a request without `status` leaves PROPOSED, REJECTED and EXPIRED out, and a proposal that expired is listed under EXPIRED and nowhere else. A row shows `question`, the status badge, `p_yes`, `volume_cr` and `close_at`, and opens the admin view of the market. A row in review turns EXPIRED at its `close_at`, and no event says so: the screen reads both lists again at the earliest `close_at` among the rows in review of its two tables (§14).
- **One market (`/admin/markets/[id]`):** an inner screen from `GET /markets/{market_id}`: the question, the status badge, the rules as on Market detail, the `resolution` when there is one, and the facts only an admin needs: `proposer_email`, `created_at`, `opened_at`, `closed_at`, `rejection_reason`, and how the market is priced, which the API sends to admins alone: the `tier` as "Tier", `b_cr` as "Liquidity b" and `max_loss_cr` as "Loss budget". A market has the three from its approval on; before that each shows an en dash. Tiers are named "Tight", "Default", "Medium" and "Custom". Then an action bar with the actions the market's `status` allows (SPEC §6):

  | Action | For `status` | Asks for | Sends |
  | --- | --- | --- | --- |
  | Approve | PROPOSED | A tier: one of the three of `/config` `tiers`, each with its `b_cr` and `max_loss_cr`, or Custom with a budget of at most `max_market_budget_cr`. The admin picks it, and none is chosen when the dialog opens. Optional changes to the wording and the two times | `POST /admin/markets/{market_id}/approve` with `tier`, `budget_cr` for CUSTOM, and each field that was changed |
  | Reject | PROPOSED | A reason, which the proposer sees | `POST /admin/markets/{market_id}/reject` with `reason` |
  | Halt | OPEN | A reason | `POST /admin/markets/{market_id}/halt` with `reason` |
  | Resume | HALTED | A note, optional | `POST /admin/markets/{market_id}/resume` with `note` |
  | Close | OPEN, HALTED | A note, optional | `POST /admin/markets/{market_id}/close` with `note` |
  | Resolve | CLOSED | The outcome, YES or NO, and the evidence | `POST /admin/markets/{market_id}/resolve` with `outcome` and `evidence` |
  | Void | CLOSED | A reason, which is published as the market's resolution | `POST /admin/markets/{market_id}/void` with `reason` |

  - Only the actions of the market's status are shown. A market that is RESOLVED, VOID, REJECTED or EXPIRED has none: a proposal whose close time passed before an admin decided on it shows the "Expired" badge and nothing to do. Each action opens a dialog, with its close cross, that asks for what its row names; text limits come from `/config` (`short_text_max_length` for a reason or note, `long_text_max_length` for the evidence and for the reason of a void).
  - Reject, Close, Resolve and Void cannot be taken back. Their dialog is an `alert-dialog` that states the consequence, and the confirm button of Reject and Void is `destructive`. Reject: "The proposer sees your reason. A rejected proposal stays rejected." Close: "Trading stops for good. After this the market can only be resolved or voided." Resolve: "Everyone who holds the winning outcome is paid. This cannot be changed afterwards."
  - **Who may settle.** Resolve and Void follow `viewer.can_settle`. When it is false, both buttons are disabled and a neutral banner above the action bar reads "You traded this market, so another admin has to settle it.", the sentence the API answers both actions with. If one of them is answered `FORBIDDEN` all the same, the dialog shows the API's `message` and the market is refetched.
  - **The void dialog** shows the market's `void_rule` as the consequence and, labelled "What a void costs VIS now", the `void_cost_cr` of the market itself, which `GET /markets/{market_id}` sends to admins. The market is read afresh when the dialog opens. While that read is on its way the figure is a skeleton and the confirm button is disabled; if it fails, the dialog shows the banner of §14 and cannot be confirmed.
  - The answer of every action is the market; the screen updates from it. `CONFLICT` and `LIMIT_EXCEEDED` show the API's `message` in the dialog, and the market is refetched.
- **Codes (`/admin/codes`):** a table of batches from `GET /admin/code-batches` (`label`, `value_cr`, `count`, `active_count`, `redeemed_count`, `revoked_count`, `expires_at`) and "New batch". The dialog asks for `label` (which counts down `code_label_max_length`), `count` (a whole number, with the help text "At most {code_batch_max_count} codes"), `value_cr` (filled in with `code_default_value_cr`, with the help text "Between {code_min_value_cr} and {code_max_value_cr} tokens"), `expires_at` and "One code per person" (`one_per_user`), and sends `POST /admin/code-batches`. Each batch has two buttons at the end of its row: "View codes" and "Revoke". "Revoke" is enabled while `can_revoke` of the batch is true: the server says whether a code is left that a revocation would stop. Nothing here names a price for a code.
  - **The codes of a batch** are shown in a dialog titled with the batch's `label`: one line that says how many codes it shows, "Download CSV" and "Print QR sheet", the codes in code style (§10) in a box that scrolls, and "Done"; "Done" and the close cross both close it. It opens by itself with the `codes` of the answer that created a batch. "View codes" opens it for any batch: it asks `GET /admin/code-batches/{batch_id}/codes` and shows the codes of the batch that can be redeemed. A code that was redeemed or revoked is not among them. When none is left, the dialog says "Every code of this batch is redeemed or revoked.", or "The codes of this batch have expired." for a batch whose `expired` is true, and its two buttons are disabled.
  - The API records every "View codes" in its audit log, and the dialog says so in `caption`: "Viewing codes is recorded." The line shows when the codes were read with "View codes", and not over the codes of the answer that created a batch, which is no recorded view.
  - When `POST /admin/code-batches` gets no answer (§14), a neutral banner above the table reads: "We did not get an answer. If the batch is in the table below, "View codes" shows its codes."
  - "Download CSV" saves a file made in the browser from the codes in the dialog: one line per code with the code, its `redeem_url`, and the batch's `label`, `value_cr` and `expires_at`.
  - The print sheet (below) is laid out from the same codes as soon as they arrive, and "Print QR sheet" waits until it is. The tap itself opens the browser's print dialog, with nothing laid out or awaited in between: a print that starts later than the tap is one a browser may refuse.
  - The codes are held in memory while the dialog is open and are written to no browser storage. Closing the dialog forgets them, and "View codes" brings them back.
  - While the codes of "View codes" are on their way, the box shows skeleton lines and the two buttons are disabled. A failure shows the banner of §14 in the dialog, with "Try again".
  - "Revoke" opens an `alert-dialog` ("Every code of this batch that has not been redeemed stops working.") with a `destructive` button, "Cancel" and the close cross, and sends `POST /admin/code-batches/{batch_id}/revoke` without `codes`.
- **Risk (`/admin/risk`):** every figure is a field of `GET /admin/risk`, shown as sent; the page compares nothing.

  | Tile | Figure | Beside it |
  | --- | --- | --- |
  | "Obligations" | `obligations.total_cr` | "Alert above" with `obligations.alert_threshold_cr`, and its three parts: "Balances" `user_balances_cr`, "Vouchers not used yet" `pending_vouchers_cr`, "Markets" `market_exposure_cr` |
  | "Loss budget" | `loss_budget_cr` | "of" `max_loss_budget_cr` |
  | "Open markets" | `open_markets` | "of" `max_open_markets` |
  | "Codes not redeemed yet" | `unredeemed_code_value_cr` | |
  | "Codes this month" | `code_issuance_this_month_cr` | "of" `max_code_issuance_per_month_cr`, and under the two a line "Adjustments this month" with `adjustments_this_month_cr`, sign included |

  - The Obligations tile is outlined in `--no` with an alert icon when `obligations.above_threshold` is true. The other tiles show their figure and its limit side by side with no warning state, because the API sends no flag for them and the frontend does not compare figures itself.
  - "Adjustments this month" is the net of what admins added to balances and took from them this month (Users, below). It stands in the tile of the codes because both are tokens VIS handed out by hand; it counts against no cap, so it has no "of". A net that is below zero keeps its minus sign.
  - Then the alerts nobody acknowledged yet (`alerts`): `message`, `created_at` and the keys and values of `context`, each with an "Acknowledge" button that sends `POST /admin/alerts/{alert_id}/ack`. Alert `severity`: INFO and WARNING are neutral banners with different icons, CRITICAL uses the error banner.
  - Then the table of `markets`, one row per market that is not settled, in the API's order. It fits 1024 px without scrolling sideways, in ten columns whose cells have 8 px at their sides: "Question" (`question`, which wraps and takes the room the others leave, with the status badge under it); "Chance" (`p_yes`); "Volume" (`volume_cr`); "Exposure" (`exposure_cr`) and "Void cost" (`void_cost_cr`), which stand before the share counts because they are what an admin reads first; "Shares" (`q_yes` and `q_no` in one cell, one under the other: "412.5 YES", "220.25 NO"); "Tier" (the name of `tier` and under it, in `caption`, "b" with `b_cr`; an en dash where a market has no tier); "Loss budget" (`budget_cr`); "Account" (`account_balance_cr`); "Closes" (`close_at`, the date and under it the time in `caption`). A row opens the admin view of the market. A figure the API sends with a minus sign keeps it. Below 1024 px the table scrolls inside its own box (§12).
- **Users (`/admin/users`):** everyone with a profile, from `GET /admin/users`. The server searches, filters and pages; the screen sends the choice and shows the rows in the order they arrive.
  - Above the table stand three controls, in one row from 768 px (the search takes the room the two selects leave) and under one another on a phone: a search field with the placeholder "Username, name or email", a Role select ("All roles", "User", "Barista", "Admin") and a Status select ("All statuses", "Active", "Suspended"). Each is a query parameter of `GET /admin/users`: the search text is `q`, the selects are `role` and `status`. An empty search field sends no `q`, and the first option of a select sends nothing. A select asks as soon as it is changed; the search asks 300 ms after the last keystroke.
  - The table (§12) puts two lines into its first two cells, so that it fits 1024 px:

    | Column | Shows |
    | --- | --- |
    | "Player" | `username`, and under it `degree_programme` in `caption`. On the admin's own row, "You" beside the username |
    | "ETH sign-in" | `name`, and under it `email` in `caption` |
    | "Role" | The word for `role`: "User", "Barista" or "Admin". A row with `bootstrap_admin: true` adds the badge "Bootstrap" |
    | "Status" | The word for `status`: "Active" in plain text, "Suspended" as a badge in the style of "Paused" (§12) |
    | "Tokens" | The token icon and `balance_cr` |
    | "Joined" | `created_at` |

    The first two columns share the width the others leave and cut their lines with an ellipsis. More rows load with `next_cursor` when the admin reaches the end of the table.
  - **A row opens the dialog of its user**, titled with the `username`, with the close cross beside the title. The dialog repeats the facts of the row one under another, which is how a phone shows all of them, and offers what the admin may do to this user:

    | Action | Offered when | Asks for | Sends |
    | --- | --- | --- | --- |
    | Suspend | `status` is ACTIVE, and the user is no bootstrap admin | A reason | `POST /admin/users/{user_id}/suspend` with `reason` |
    | Reinstate | `status` is SUSPENDED | A reason | `POST /admin/users/{user_id}/reinstate` with `reason` |
    | Change role | The user is no bootstrap admin | A select of "User", "Barista" and "Admin" (`USER`, `BARISTA`, `ADMIN`) | `POST /admin/users/{user_id}/role` with `role` |
    | Adjust tokens | Always | "Add" or "Remove", an amount and a reason | `POST /admin/users/{user_id}/adjustments` with `amount_cr`, `reason` and an `Idempotency-Key` |

    A tap on an action puts its step in the place of the facts, with a ghost "Back" that returns to them. The steps are headed "Suspend account", "Reinstate account", "Change role" and "Adjust tokens". Their fields are labelled "Role", "Amount" and "Reason", a reason with the help text "Kept in the audit log.", and the Add / Remove control is named "Add or remove tokens". The three controls above the table are labelled "Search", "Role" and "Status". **Decision:** the actions stand in the dialog, not in the row. Three buttons in every row would push the table past 1024 px and off a phone's screen, and nothing here is done so often that it has to be one tap away.
  - **Suspend** is confirmed in its step, which states the consequence ("{username} is locked out of MarkETH until an admin reinstates the account. Nothing is taken from it: its tokens, positions and vouchers are kept.") and asks for the reason. The reason is required and goes into the audit log, not to the user. The button is `destructive`, and while this step is shown the dialog is a confirmation dialog (§12): a tap on the scrim does not close it. **Reinstate** says "{username} can use MarkETH again.", asks for a reason in the same way, and has a primary button.
  - **Change role** shows the select, set to the user's role, and one line that follows the choice: "Trades and proposes markets." for User, "Also scans vouchers at the bar." for Barista, "Also runs the admin area: markets, codes, risk and users." for Admin. "Save role" is disabled until another role is chosen. The select posts nothing by itself (§14).
  - **Adjust tokens** has two steps. The first asks: a segmented control "Add" and "Remove" (§12), an amount field that takes digits and one decimal point, and a reason, which is required. "Review", disabled until an amount and a reason are typed, leads to the second, which restates the adjustment in one sentence before anything is sent: "Add {amount_cr} tokens to {username}?" or "Remove {amount_cr} tokens from {username}?", with the reason under it, a primary "Add tokens" or "Remove tokens", and "Back". The confirming tap sends the amount as it was typed, with a minus sign in front of it for "Remove", and the reason. The reason goes into the audit log; the user's Activity shows the amount under the API's label and not the reason. The request and its `Idempotency-Key` are kept as §14 keeps those of a trade.
  - **Who cannot be changed.** A row with `bootstrap_admin: true` is an admin the server's configuration names. Its dialog says "This admin is named in the server's configuration. The role cannot be changed and the account cannot be suspended here." and offers neither "Suspend" nor "Change role". The admin's own row is the one whose `id` is the `id` of `/me`: it opens no dialog and offers nothing, because an admin does none of this to their own account.
  - The API alone decides each action. A 2xx closes the dialog and shows a toast ("Account suspended", "Account reinstated", "Role saved", "Tokens adjusted"). The table then shows what the answer says: the user's row after a suspension, a reinstatement or a change of role, and the answer's `balance_cr` after an adjustment. A refusal shows the API's `message` in the dialog, on the field it names or above the button, and the dialog stays open: `FORBIDDEN` for the admin's own account or a bootstrap admin, `CONFLICT` for an account that is suspended or reinstated already, `INSUFFICIENT_BALANCE` for a removal of more than the account holds, `LIMIT_EXCEEDED` for an addition above the most one adjustment may add, `VALIDATION_ERROR` for a reason or an amount the API does not take.

States: every table has skeleton rows while loading and a banner with "Try again" on error. An empty table is one plain sentence: "No proposals waiting.", "No markets.", "No batches yet.", "No alerts.", and "No users match." for a search or a filter that leaves nobody. The risk page refetches on focus; nothing on it is live.

**The print sheet** is laid out for A4, upright, with 12 mm margins, and it is all that reaches the paper: the top bar, the tabs, the tables and the dialog are not printed. The page is paper, the one light surface in the product, and the code cards on it are pieces of the app: the palette of §4, the type of §5 and the components of §12, and nothing drawn for the sheet alone. `voucher-design.md` states the look this section specifies.

- **The page** is unprinted paper with `--ink` on it. At the top of the first page: `logo-lockup-dark.png`, 10 mm tall, at the left; at the right, on two lines that end at the right edge, the batch's `label` in `label` and "Valid until" with `expires_at` in `caption`. A 1 px line in `--ink` across the sheet, 4 mm under the two, parts the head from the cards, which begin 6 mm under it.
- **The grid.** Three columns of 58 mm, 6 mm apart, in the order of the codes. The columns are fixed: a row that holds one or two cards leaves the rest of it empty, and no card is stretched.
- **A card** is a `--card` surface with `--radius-lg` and 3 mm of padding, its text in `--foreground`. It holds, 3 mm apart:
  1. A top line like the top bar of the app: `logo-lockup.png`, 5 mm tall, at the left, and at the right `value_cr` as the balance pill shows a balance: the token icon on its disc and the figure, on a `--raised` pill.
  2. The QR code of the code's `redeem_url`, centred, on its `#FAFAFF` tile with `--radius-lg`, 40 mm wide: the QR of §12, with `--ink` modules and the quiet zone of four modules, at the width paper needs.
  3. The code in code style (§10) at the size of `number`, and under it "Valid until" with `expires_at` in `caption` and `--muted-foreground`, both centred.

  A card that is cut out says by itself what it is, whose it is, what it is for and until when.
- A card has no outline, no shadow, no gradient and no notch. Dashed hairlines in `--ink` in the middle of the gaps between the cards show where to cut.
- The sheet asks the browser to keep its fills (`print-color-adjust: exact`), so a card is navy on paper whether or not "Background graphics" is ticked in the print dialog.
- A card is never divided between two pages: a row that does not fit moves to the next page whole. A4 takes three rows on a page.
- The sheet is made in the browser from the codes in the dialog. Nothing is sent anywhere to print it.

### Barista (`/barista`)

Built to be used at arm's length, fast, by someone holding a cup. For roles BARISTA and ADMIN; anyone else sees the "not found" screen. A barista is a player an admin gave that role (§11).

1. The scanner fills the screen below the top bar. A "Type the voucher code" button is below it. It opens a sheet (§12) with the voucher-code field, a primary "Redeem voucher" and the close cross.
2. A scan, or a typed code, is sent as `code` to `POST /barista/vouchers/redeem`. The scanner pauses until the answer is there, then a full-screen result shows for 2.5 s and the scanner returns:
   - **Accepted:** `--yes` background, `--ink` text, a large check icon, the `product_name` in `display`, then "Voucher accepted" and the token icon with `value_cr` in `number-lg`, and at the bottom "Paid in predictions." The product is the largest thing on the screen: it is what the barista hands over.
   - **Rejected:** `--no` background, `--ink` text, a large cross icon and "Voucher not valid". No reason is given (SPEC A.6). This is the result for `CODE_INVALID` and for a `VALIDATION_ERROR`, which is a scan that cannot be a voucher code.
   - **Redeemed a moment ago:** the result for an answer with `repeat: true`. The voucher is one this barista redeemed a moment ago, scanned again: the answer to the first scan may not have arrived, or the same QR came under the camera twice. `--raised` background, `--foreground` text, neither green nor red, because it is no second voucher and no refusal. A large clock icon, the `product_name` in `display`, then "Redeemed a moment ago at this bar" with the time of day of `redeemed_at` ("18:02", §10) in `number-lg`, the token icon with `value_cr`, and at the bottom "It counts once. Hand it over if you have not yet." The barista hands the product out once, whichever of the two scans showed it.
3. Each result also triggers a short vibration where supported: one pulse for accepted, two for rejected, one long pulse for redeemed a moment ago.
4. A voucher that stays in front of the camera is one request, however long it is held there and whatever else is read in between. It is asked about again once the scanner has watched for two seconds without reading it. Time under a result or under the typing sheet does not count: a voucher is not away because the screen was busy with its answer.
5. Any other failure is no verdict on the voucher. It shows the banner of §14 over the scanner, never the red screen, and the barista scans again. If the scan that got no answer did redeem the voucher, the API answers the next scan of it by the same barista with `repeat: true`, and the third result says so. The API does that for a short time only. After it, and at another bar, the answer is "Voucher not valid".

This is the only screen that uses a full-bleed green or red surface; the result must be readable from across a counter. The third result is full-bleed too, in a colour that is neither.

### Not found (any other path)

Inside the app shell: the Polybahn car, "This page does not exist. Not even in the HG basement." and a secondary "Browse markets" button. It stands for a path the app does not have, for a market the API answers `NOT_FOUND` for, and for the Admin and Barista routes opened without their role.

## 14. States and feedback

### Every screen has four states

Loading (skeletons), empty (icon, sentence, at most one button), error (banner with "Try again") and content. A screen is not finished until all four exist. A screen that asks the API for nothing has content alone: the usage policy, and "not found".

### Errors

The API returns `{code, message, details}`. The `message` is written for the user and is shown as sent; the frontend branches on `code` only to decide where and how to show it.

| Code | Where it appears | Treatment |
| --- | --- | --- |
| `PROFILE_REQUIRED` | Start-up | The profile set-up screen (§13), with `details.name` and `details.email`. No error styling: the visitor has no profile yet |
| `USERNAME_TAKEN` | Profile set-up, Profile | On the username field, with the API's message |
| `QUOTE_STALE` | Trade sheet | New quote from `details.quote`, inline notice, confirm again |
| `INSUFFICIENT_BALANCE` | Trade sheet, voucher sheet, adjustment of tokens | With the shares entry when the quote of a number of shares says it, above the button when the trade does; either way the market is read again. The voucher sheet shows it above its button and refetches the products. The adjustment dialog shows it above its button: the removal is larger than what the account holds |
| `INSUFFICIENT_SHARES` | Trade sheet (sell), from the quote or the trade | Inline above the button, then refetch the market so the sell shortcuts are current |
| `LIMIT_EXCEEDED` | Trade sheet, propose form, redeem sheet, admin dialogs | Inline. On the trade sheet with the shares entry when the quote says it and above the button when the trade does; either way the stake-left line is refreshed from `details` and the market is read again. In the adjustment dialog above its button: the addition is more than one adjustment may add |
| `TRADE_TOO_SMALL` | Trade sheet | With the shares entry when the quote says it, above the button when the trade does |
| `MARKET_NOT_OPEN` | Trade sheet, from the quote or the trade | Inline above the button, then refetch the market so the screen shows its real status |
| `CODE_INVALID` | Redeem sheet, barista | Inline on the field; the rejected screen on the scanner. Always the same wording |
| `RATE_LIMITED` | Redeem, trade, quote; any request when the proxy's own limit is hit | Inline where an action was refused, otherwise the banner; the action is disabled for `details.retry_after_seconds` with a countdown |
| `VALIDATION_ERROR` | Forms, the shares entry, leaderboard filters, admin dialogs, barista | On the field named in `details`: `details.field`, whose sentence is the error's `message`, or the last element of `loc` in an entry of `details.errors`, whose sentence is that entry's `msg`. Both are written for the person who filled the field in and are shown as sent. The field `policy_accepted` is the checkbox. Without a field, above the submit button. On the scanner, the rejected screen |
| `UNAUTHENTICATED` | Anywhere | The full-screen message (§13) with the API's message and "Try again", which reloads. The request reached the API without the portal's identity; no screen can repair that |
| `FORBIDDEN` | Anywhere | The API's message as a banner, or inline in the sheet or dialog that asked: there an admin reads why an account cannot be changed. At start-up (an address MarkETH is not open to, a suspended account) the full-screen message |
| `NOT_FOUND` | Routes, "Show QR", admin dialogs | The "not found" screen. For a voucher, a batch or a user, inline in the sheet or dialog, then refetch the list behind it |
| `CONFLICT` | Admin actions, "Show QR" | Inline in the dialog or sheet, then refetch. For a voucher it means the voucher is used up or has expired |
| `IDEMPOTENCY_KEY_REUSED` | Trade sheet, adjustment of tokens | Should not occur. Show "Something went wrong. Try again." in place of the message and create a new key for the next attempt |
| `SERVICE_UNAVAILABLE` | Anywhere | Banner with the API's message ("This is temporarily unavailable. Try again shortly."). Code redemption needs Redis; trading does not |
| `INTERNAL_ERROR` | Anywhere | Banner with the message and "Try again" |

Two answers are not the API's and are told apart by their body. Every API call asks for JSON (`Accept: application/json`), and the portal answers such a call it has no session for with a 401 whose body is the empty object `{}`. That 401 means reload: the page reloads once, the portal signs the visitor in and returns to the same address. If the reload brings the same answer, the full-screen message of §13 is shown. Any other answer without a `code` (an error page of the proxy, a body that is not JSON) is treated as `SERVICE_UNAVAILABLE`, with the banner "This is temporarily unavailable. Try again shortly."

A network failure with no response uses the same banner: "No connection. Try again."

### A submitted trade

A trade carries an `Idempotency-Key`: the same key with the same body returns the same trade and never executes a second one (SPEC §8). The trade sheet therefore keeps the two together.

- Confirm serialises the request body once, makes one key for it and keeps both.
- Both are kept until an answer says what became of that request: a 2xx (the trade exists) or a 4xx other than 429 (the trade was refused and nothing was booked). Then they are dropped, and the next Confirm builds another body with another key. `QUOTE_STALE` is such a 4xx: the quote of `details.quote` leads to a different body.
- Every other outcome says nothing about the trade, and both are kept: any 5xx, a 429, the portal's 401 and a request without a response. The message is shown inline, after a 429 with the countdown of `details.retry_after_seconds`, and the button reads "Try again". A tap sends the stored body with the stored key again, unchanged. A body rebuilt from a later quote would be another request and could trade a second time.
- **Decision:** while the two are kept, the sheet offers nothing but "Try again". The Buy / Sell control, the outcome toggle, the shares entry and the sell shortcuts are disabled, showing the trade as it was sent, and closing and reopening the sheet shows the same unanswered trade. Nothing is sent again without a tap.
- While a request is on its way, from Confirm or from "Try again", the sheet cannot be closed at all (§13): its answer is a verdict or another "Try again", and either is shown on the sheet that sent it.
- They are held in memory, not in browser storage. A reload forgets them, and so does the reload that follows the portal's 401; the market, the balance and the History then show what the server recorded.

### An adjustment of tokens

`POST /admin/users/{user_id}/adjustments` carries an `Idempotency-Key` as well, and the dialog that sends it keeps the two together exactly as the trade sheet does: one body and one key from the confirming tap, kept until a 2xx or a 4xx other than 429, sent again unchanged by "Try again" after anything else, and held in memory only. While they are kept, the dialog shows the adjustment as it was sent and offers nothing but "Try again"; closed and opened again for the same user, it shows the same. After a reload, the user's row in the Users tab shows the balance the server recorded.

### Other writes without an answer

A write whose answer never arrived (no connection, a 5xx) may or may not have happened. The screen does not guess and does not send it again by itself: it refetches what the write would have changed, shows that, and only then lets the user act again.

| Write | Before the user may send it again |
| --- | --- |
| `POST /vouchers` | `GET /me/vouchers` and `GET /me` are refetched (§13, Wallet). A voucher that was issued is in the list, and "Show QR" gives it a code |
| `POST /codes/redeem` | `GET /me` and `GET /me/ledger` are refetched: the balance and the Activity show whether the code counted |
| `POST /markets` | `GET /me/proposals` is refetched: a proposal that arrived is in the list |
| `POST /admin/code-batches` | `GET /admin/code-batches` is refetched, and a banner says what to look for (§13). A batch that arrived is in the table, and "View codes" shows its codes |
| `POST /me/notices/{notice_id}/seen` | Nothing is read and nothing is sent again. The page does not show that notice a second time; a later page load tells it once more and marks it then |
| Suspending, reinstating, changing a role | `GET /admin/users` is refetched: the row shows the status and the role the server holds |
| `POST /admin/users/{user_id}/adjustments` | Never built again: the stored body and key are sent again (above) |
| Another admin action | The market or the risk page it acts on is refetched |
| `POST /vouchers/{voucher_id}/code`, `PUT /me/profile` | Nothing: sending either again is harmless |
| `POST /barista/vouchers/redeem` | Nothing can be refetched. The barista scans the voucher again: if the first scan redeemed it, the API answers `repeat: true` for a short time, and §13 says what the scanner shows |

### Live data

The server pushes over one event stream, `GET /stream`, and a tab holds one stream for everything it shows. Nothing on screen says whether the stream is open: no indicator, no banner, no badge, no control, and no sentence. A live value looks like the same value read a moment later.

- **One stream per tab.** A tab holds its stream while its page is visible. A screen says which topic it shows, and the stream follows what the screens on the page name together: the Markets list names `markets`, Market detail its market (`market:{id}`), the Leaderboard `leaderboard`. What is the caller's own arrives on every stream without being named. When the topics change (a navigation), the tab ends its stream and opens one for the changed set.
- **Events are hints, not truth.** A screen never works a figure out from an event. A price an event carries is written as it arrived, and only where its `version` is newer than what is on screen; everything else is asked for again.

  | Event | Shown at once | Asked again |
  | --- | --- | --- |
  | `market.quote` | The Price, "Vol." and a point at the end of the chart of that market, on Market detail and on every card | The market, for the viewer's side (below) |
  | `market.status` | Nothing | The market and its chart; every list of markets |
  | `leaderboard.changed` | Nothing | The leaderboard: every row the list has loaded, the podium and the pinned row |
  | `me.changed` | Nothing | `/me` and everything under it, the products of the voucher sheet, and the market that is on screen |

- **Whenever a stream opens** (the first of a page, one that follows a loss, one for changed topics), the tab reads once what its topics cover, `/me` with everything under it, and the products. Nothing that was pushed before is sent again, so this read is what closes the gap.
- **The spread.** An announcement is answered after a random wait of up to `live_announce_spread_ms` (from `/config`), and one read answers every announcement of the same thing that arrives during the wait or the read. The people who watch one thing therefore do not all ask in the same instant. The read that follows a trade of the user's own does not wait (§13).
- **The viewer's side.** While a market is on screen and the viewer has bought into it, a `market.quote` is followed by one read of the market, because what a holding is worth (`value_cr`) and the sell chips are the server's to work out: after a random wait of up to `live_viewer_refresh_seconds`, once, however many quotes arrive meanwhile. If quotes overtook that answer, the newer price stays on screen.
- **An open quote is never touched.** No event and no poll replaces or drops a quote of the trade sheet. The user confirms what they were shown, and the server answers `QUOTE_STALE` if the price moved past the guard.
- **The caller's own.** Balances change without the user acting: a payout or a refund arrives when an admin settles a market, and monthly tokens and voucher refunds are booked lazily (SPEC A.3). Each reaches its owner as `me.changed`, and `/me` is read again: the pill of the top bar (§11), the notices (§13), the Wallet with its vouchers and its Activity, where a payout shows as a ledger row without a reload, and "Paid out" and "Refunded" in the Portfolio follow. While the stream is open, `/me` is therefore not read on navigation. While it is not open, `/me` is read on every navigation from one screen to another. A change of the query string alone (a sort of the Leaderboard, an open trade sheet) is no navigation. The trade sheet and the Wallet read `/me` when they open, either way.
- **The Leaderboard** follows `leaderboard.changed`, which the server sends at most every few seconds while the ranking moves. The rows on screen are read again in the order the API returns them. A figure that changed flashes (§7); a row that changed its place stands in its place, nothing slides, and the page keeps its scroll position. An open sheet of a player's statistics shows the fields of that player's row as the list holds them, so it follows as well.
- **When the stream cannot be held.** A stream is lost when it is refused or answers an error (the server holds as many streams as it may, the caller holds too many, a 503), when it ends, when nothing is heard for twice `live_heartbeat_seconds`, and in a browser without `EventSource`. Nothing on screen says so, and there is no error banner for it: the user sees slightly slower updates. After a loss the tab polls: the first poll after a random wait of up to `live_poll_fallback_seconds`, then every `live_poll_fallback_seconds`, never two polls closer than that. A poll reads again what the tab's topics cover and `/me`. At every poll the tab tries the stream again, unless the browser is reconnecting by itself, and the first stream that opens ends the polling.
- A poll is an ordinary API call, so it doubles as a check of the portal's session: if the portal answers it with its 401, the page reloads (§ Errors).
- **A hidden page** neither holds a stream nor polls. When it is shown again its stream opens, and the read that follows brings what was missed.
- **Close time.** No event says that a market has reached its `close_at`: `market.status` follows what admins and the circuit breaker do. A screen that shows a market as open for trading therefore asks again at that moment: Market detail at the `close_at` of its market, the Markets list at the earliest `close_at` among the cards it shows with `trading_open` true (§13). A proposal in review turns EXPIRED at its `close_at`, and no event says that either: Market detail asks at the `close_at` of a proposal it shows, My proposals at the `close_at` of its rows in review, and the admin's Markets screen at those of the rows in review in both of its tables, reading both lists again. A row those two screens show as open is not timed. The timer runs by the device's clock, asks the API and decides nothing, and it asks again every `live_poll_fallback_seconds` while the answer still shows the same state. What the screen shows afterwards is the answer's `status` and `trading_open`, never the device's clock.
- Live are the Markets list, Market detail, the Leaderboard, the balance and the notices. Every other figure is read when its screen opens and when the window regains focus.

### Buttons that change state

Every button that posts is disabled from tap until response. Destructive admin actions additionally go through a confirmation dialog. Nothing in the product is submitted by a page load or a prefilled field alone: there is always a deliberate act, which is a tap or, at the bar, the scan of a voucher. A select, a picker, a slider and a checkbox post nothing by being changed: they set what a button then sends. The one write no act sends is the mark of a notice: when the app has shown a notice, it tells the API so (§13).

**A second submit is stopped where it is made.** The disabled button is what the user sees. It is not the guard: the screen learns that its request went out a moment after it did, and two submits in that moment (Enter and a click, a tap that lands twice) would both find the button enabled. Every submit therefore asks, in the same step in which it would send, whether a request of that write is out: the cache of writes that are on their way, or a mark the handler set itself. What the last render knew is never the check. A trade and an adjustment of tokens are guarded by the request they keep (above).

## 15. Accessibility

- **Contrast:** text meets WCAG AA using the pairs in §4. A new colour pair is measured before it is used.
- **Colour blindness:** green and red always come with a word, a sign or an icon. The outcome buttons keep a fixed order, YES on the left.
- **Focus:** a 2 px `--ring` outline on `:focus-visible` for every interactive element. Sheets and dialogs trap focus and return it to the opener on close.
- **Semantics:** one `h1` element per screen: the title in the top bar of an inner screen, the question on Market detail and on the admin view of a market, the title of a main screen that shows one (Leaderboard), and otherwise a visually hidden one that names the screen. The styles `h1` to `h3` of §5 are looks, not levels. The tab bar is a `nav`; the outcome toggle and the filter chips use `aria-pressed`; a column heading that sorts carries `aria-sort`; form errors use `aria-describedby` and `aria-invalid`.
- **Live regions:** the live Price of Market detail is in an `aria-live="polite"` region limited to one announcement per 10 s. The `QUOTE_STALE` notice, the refusal of a number of shares and trade errors use `role="alert"`. The receipt banner of a trade is no live region: it takes the focus when it appears.
- **The balance:** the pill of the top bar keeps its name, "Wallet, {balance_cr} tokens". Beside it stands a visually hidden region, `aria-live="polite"` and `aria-atomic`, that says "You have {balance_cr} tokens." when the balance changes, at most once per 10 s, the pace of the live Price. The word follows the string (§10). It says nothing when the page loads. A balance that moves by itself is thereby told to someone who cannot see the flash.
- **The Markets list is not a live region.** A card's Price reads "YES 54.8 percent" when a reader reaches it; twenty cards announcing themselves would drown the page. **The Leaderboard is not a live region** either: its measure line stays the only polite region of the screen, and a row reads its current rank and figure when a reader reaches it.
- **Shares entry:** the three controls form a group named "Shares". The field is named "Number of shares" and the slider "Whole shares". The slider says its value as "40 shares", and as "not set" while the field holds a number it cannot show. The stake-left line and the refusal describe both (`aria-describedby`), and the field is `aria-invalid` while the server refuses its number. The shortcuts are a group named "Shortcuts"; each chip is named by its shares ("10 shares") and carries `aria-pressed`. The sell chips are a group named "Shares to sell"; a sell chip is named by all it shows: "Half: 25 shares for 12.73 tokens".
- **Quote panel:** a description list. An amount is read as its number and "tokens", the price per share as "0.506 tokens per share", and a row without a figure as "none yet".
- **Receipt banner:** it takes the focus when it appears, without scrolling the page, and is read as one sentence: "Bought 50 YES shares for 25.312468 tokens, 0.506 tokens per share". The next Tab reaches Buy / Sell.
- **Picker:** from 768 px a combobox named by its label, whose list is a listbox with the chosen option marked selected; below 768 px a button named "{label}: {chosen label}" or "{label}: {placeholder}" that opens a dialog titled by the label, with a search field named "Search levels" or "Search programmes". A change of the query is told once in a polite region: "{n} results", "1 result" or the sentence for no match (§12).
- **Close cross:** a button named "Close" in every sheet and every dialog, reachable with the keyboard. The Escape key does what it does. It is disabled while a request the surface sent is out (§12).
- **Checkbox:** named by its whole text ("I accept the usage policy"). The link inside it is a stop of its own, named "usage policy", and says that it opens in another tab.
- **Barista results:** each of the three full-screen results is announced when it appears. "Redeemed a moment ago at this bar" is read with its time, so that a barista who does not look hears that nothing was redeemed twice.
- **Podium:** read as a list of three in the order first, second, third. A name that is cut on screen is read whole, and an empty place is hidden from a screen reader.
- **Leaderboard rows:** a row of the ranking, a filled place of the podium and the pinned row are each a `button` with `aria-haspopup="dialog"`, named by what it shows; it opens the statistics of its player (§13).
- **Toast:** the link "View" of a notice is a text link of its own, a 44 px target that the keyboard reaches.
- **Screen-reader text for figures:** the Price reads "YES 54.8 percent". An amount reads "25 tokens". A price per share reads "0.506 tokens per share". A missing rate reads "none yet", not the dash.
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
- Give every sheet and every dialog its close cross, also when it only reports what was done. Disable it, and every other way out, while a request the surface sent is out.
- Stop a second submit in the step that would send it, by asking what is on its way.
- Ask the API again when the `close_at` of a market on screen passes, and show what it answers.
- Check the Leaderboard at 375 px, 768 px and 1024 px with a username of the greatest length and with one, two and three players: nothing lies over anything else.
- Give every amount of tokens its token icon, on a sell as on a buy; give a number of shares its word and no icon.
- Leave the trade sheet open after a trade, with its receipt as a banner and its form ready.

Don't:

- Compute, compare or round a price, balance, limit or share quantity in the frontend. The one test an amount gets is the one for the string `0` (§10).
- Hard-code a tunable ("100 tokens", "35 tokens", "30 days") in copy or logic.
- Judge a typed number of shares in the frontend, or read the ends of the slider as a limit to check it against.
- Put a "Done" between a finished trade and the next one.
- Show whether the stream is open.
- Work a figure out from an event.
- Read the device's clock to decide whether a market can be traded, or to end the chart: `trading_open` and `until` are the server's.
- Guard a submit with what the last render knew.
- Show a tier, a liquidity figure or a loss bound to a player.
- Post from a select, a picker, a slider or a checkbox alone.
- Use green or red for anything that is not YES, NO, a gain, a loss, an error or a destructive action.
- Put white text on a green or red fill, or pure red text on a card.
- Add a gradient of any kind, a light theme, a shadow on a card, a blur, or a remote image or font.
- Build anything from the "must not look like" table in §1: landing sections, marketing copy, glass, blobs, sidebars, pill-shaped everything, confetti.
- Use a trophy JPEG, or a trophy for anything but one of the first three places of a "Highest first" leaderboard.
- Use the ETH logo, or type the name beside the logo with name.
- Use pure white `#FFFFFF` or pure black `#000000` anywhere on screen.
- Put a joke in an error, a balance, the trade sheet, the usage policy or the admin area, or more than one campus line on a screen.
- Add any screen, button or wording about buying tokens, codes or vouchers, or name a price for one.
- Show "CR", "credits" or "vouchers" as the unit's name, or the bean without its white disc.
- Say why a code or voucher was refused.
- Show an email address or a real name outside the viewer's own profile and the admin area.
- Sort, filter, rank or total leaderboard rows in the frontend, or look at the balance to decide whether a product can be had.
- Build a sign-in, a sign-out or a signed-out state. The session belongs to the portal.
- Keep a code or a voucher code in browser storage, or leave a code in the address.
- Rebuild the body of a trade or of an adjustment of tokens that got no answer, or give it another Idempotency-Key.
- Leave a `dark:` class, a shadow or a raw colour in a component copied from shadcn/ui.
