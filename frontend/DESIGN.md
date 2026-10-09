# MarkETH design system

The visual and interaction rules for the MarkETH frontend. SPEC §11 says which screens exist and SPEC A.11 fixes the direction: Polymarket's layout, a dark navy theme, the MarkETH logo. This file is the full system those two sections point to.

## 1. How to use this file

- Build a screen from §13, using only the components in §12 and the tokens in §9.
- The tokens in §9 are the contents of `frontend/src/app/globals.css`. A colour, font or radius changes in both places in the same commit.
- Components never contain a raw hex value, pixel font size or ad-hoc radius. They use the Tailwind classes the tokens generate (`bg-card`, `text-yes-text`, `rounded-lg`).
- Decisions this file takes that the spec left open are marked **Decision**. Changing one means editing this file first.
- Where this file and `docs/SPEC.md` disagree on behaviour, the spec wins and this file is wrong.

### Reference: Polymarket

Polymarket is the model for layout and interaction. When this file is silent, do what Polymarket's mobile site does, in MarkETH's colours.

| Taken from Polymarket | Where |
| --- | --- |
| Dark, flat surfaces separated by tone and hairlines, not shadows | §4, §6 |
| Market cards: question, a large "% chance" figure, soft-tinted "Yes" and "No" buttons, volume in the footer | §12 |
| Market page: question, large chance figure, line chart, then the trade ticket, then rules | §13 |
| Trade ticket: Buy / Sell tabs, outcome buttons that carry their price, quick-amount chips, one wide action button | §13 |
| Logo with the name at the top left of every main screen | §3, §11 |
| Bottom tab bar on phones; a ticket panel beside the chart on desktop | §6, §11 |
| White percentage figures; green and red kept for the Yes and No controls | §4 |

| Deliberately different | Why |
| --- | --- |
| No "to win" or profit figure, no cents | The API sends none and the frontend may not compute one (golden rule 6) |
| No deposit, cash-out or payment screens | Nothing is for sale (golden rule 9) |
| No comments, categories, search, images or leaderboard | Not in the API (SPEC A.1) |
| A coffee-bean token in place of the dollar sign | The unit is a token worth one coffee |
| Pixel-art campus touches and ETH in-jokes (§3) | Polymarket is neutral; MarkETH belongs to one campus |
| Stake presets only, no free amount when buying | SPEC §4 stake limit; presets come from the server |

## 2. Principles

1. **Phone first.** Every screen is designed at 375 px wide and one thumb. Wider layouts are the phone layout with more room, never a different product.
2. **Numbers are the content.** The probability is the largest thing on any screen that has one. Chrome stays quiet so numbers can be loud.
3. **The server owns every number.** The frontend formats what the API returns and never computes a price, balance, limit, profit or eligibility (root `CLAUDE.md`, golden rule 6). If a screen needs a number the API does not send, the screen does without it.
4. **Calm navy, scarce colour.** The canvas is navy, the accent is the off-white `#FAFAFF`. Green and red appear only where they mean YES, NO, gain or loss.
5. **One primary action per screen.** There is at most one white button visible at a time.
6. **Serious numbers, playful edges.** The trade ticket, balances and errors are sober. Personality lives around them: the bean, the pixel art, a campus joke in an empty state (§3).
7. **Nothing is for sale.** No screen, button or sentence suggests buying tokens or codes (golden rule 9, SPEC §2). Avoid the words "buy tokens", "top up", "deposit", "cash out" and any currency symbol.

## 3. Brand

### Name

- In text the product is written **MarkETH**: capital M, capital ETH, one word.
- Inside the logo the name is set as "markETH", with a lowercase m. That spelling exists only in the logo image; it is never typed.
- The ETH Zurich logo is never used (SPEC A.11). A VIS logo may appear only in the footer of the Login screen, and only with VIS's agreement.

### Logo

There are two forms of the logo, both supplied by the team.

| Form | What it is | Where it is used |
| --- | --- | --- |
| **Logo with name** | The cup with the rising arrow, followed by "markETH" | The top left of the top bar, as on Polymarket; the Login screen; the printable QR sheet |
| **Logo** | The cup with the rising arrow alone | Everything that identifies the app in a square: browser tab, home-screen icon, splash, and anywhere the name does not fit |

The originals are kept, untouched, in `frontend/design/`. The files the app serves are derived from them and live in `frontend/public/brand/`. Images must be served by the app itself: the Content-Security-Policy allows no remote image.

| File in `frontend/public/brand/` | What it is | Use |
| --- | --- | --- |
| `logo-lockup.png` | Logo with name in `#FAFAFF`, transparent, 716 x 168 px | Top bar (28 px tall), Login (48 px tall) |
| `logo-lockup-dark.png` | Logo with name in `--ink`, transparent | The printable QR sheet only |
| `logo-mark.png` | Logo in `#FAFAFF`, transparent, 320 x 320 px | Where only a square fits, for example a narrow header beside a back arrow |
| `logo-mark-dark.png` | Logo in `--ink`, transparent | The printable QR sheet only |
| `icon-512.png`, `icon-192.png` | `#FAFAFF` logo on `#1E2749`, square | Web manifest (home-screen icon) |
| `apple-touch-icon.png` | The same, 180 x 180 px | iOS home-screen icon |
| `favicon.png` | The same, 48 x 48 px | Browser tab |
| `token.png` | The token, transparent, 256 x 256 px | The token icon (below) |

- The originals are black, which is invisible on navy. The app uses the same shapes in `#FAFAFF`; nothing but the colour is changed.
- The name in the logo is part of the image. It is not rebuilt from a font, and no text is placed beside the logo with name. The image carries `alt="MarkETH"`.
- The logo sits only on `--background` or `--card`. Clear space on every side is at least half the height of the cup. Never stretch, rotate, outline, animate or add effects, and never tint it green or red.
- The minimum height of the logo with name is 24 px. Below that, use the logo alone.
- A new logo replaces the originals in `frontend/design/`, and the served files are regenerated from them at the sizes above.

### Token

The unit of the app is the **token**: one token is one coffee. Its symbol is a pixel-art coffee bean.

- The **token icon** is the bean centred on a `#FAFAFF` disc, the bean filling 70% of the disc. The bean's dark outline disappears on navy, so the bean never sits on a navy surface without its disc.
- Sizes: 16 px beside `number` and `body-sm` figures, 20 px beside `number-lg`, 32 px beside `display`, 64 px in the welcome dialog and empty wallet. The gap to the figure is a quarter of the icon's size.
- The icon comes **before** the figure: (bean) 0.25. In tables in this file, "(bean)" stands for the token icon.
- The icon is decorative (`alt=""`); the accessible text of an amount is always the number and the word "tokens" (§15).
- The bean's brown is not a design token. No text, border or surface uses it.

### Campus touches

Polymarket's structure, with the humour of one campus. These elements make MarkETH feel like it belongs to ETH without using ETH's logo or typeface.

**Pixel art.** The token is pixel art, so pixel art is the illustration style of the whole product. Spot illustrations are drawn on a 16 x 16 or 24 x 24 grid, shown at whole multiples of that grid with `image-rendering: pixelated`, and coloured only with `#FAFAFF`, `--muted-foreground`, `--raised` and the bean's browns. They live in `frontend/public/brand/pixel/`.

| Illustration | Where |
| --- | --- |
| Empty coffee cup | Empty portfolio, empty wallet activity |
| Polybahn car | Page-level loading (below), "not found" screen |
| Main building dome | Login footer, empty Markets list |
| Steaming cup | Coffee ticket issued, barista "accepted" |
| Lecture-hall blackboard | Propose market, "In review" proposals |
| Alarm clock | Closed market waiting for its result |

These files are not drawn yet. Until one exists, its place is taken by the matching 32 px lucide icon; no screen waits for an illustration.

**Beans in motion.** The bean is the one thing that may move for fun (§7): beans drop into the balance when tokens arrive, and a single bean bounces on a finished trade.

**The Polybahn loader.** A route change that takes longer than 300 ms shows a 2 px `#FAFAFF` progress line under the top bar with the pixel Polybahn car riding its front edge.

**Campus copy.** Empty states, the welcome dialog, the "not found" screen and success toasts may carry one dry campus reference. Examples of the register:

| Place | Copy |
| --- | --- |
| Empty Markets list | "No markets yet. Quieter than the HG on a Sunday." |
| Empty portfolio | "No positions yet. Your cup is empty." |
| Not found | "This page does not exist. Not even in the HG basement." |
| Welcome | "You got {signup_grant_cr} tokens. Spend them wisely, or on coffee." |
| Coffee ticket issued | "Show this at the bar. You earned it, sort of." |
| Barista accepted | "Enjoy your coffee" |
| Closed, awaiting result | "Trading is over. Now we wait, like for exam results." |
| Rate limited | The API's message, unchanged |

Rules that keep it from getting tiresome:

- At most one playful element per screen, and none inside the trade sheet, a form in progress, or the admin area.
- Errors, balances, limits and anything about why a trade or code failed are never joked about. They show the API's message.
- References are to places and shared experiences (HG, CAB, Polyterrasse, Polybahn, Mensa, ASVZ, exam session). Never to named people, departments' reputations, grades of anyone, politics or religion (SPEC §6 content rules apply to our copy too).
- Jokes are written once and reviewed like any other copy. They are static strings, not generated.
- Nothing playful blocks a tap, delays a response or replaces a label.

### Voice

- English only. Short, plain, second person: "You have 5 tokens.", "The price moved."
- Error text comes from the API's `message` field and is shown as sent (§14).
- The unit is called **tokens** everywhere in the interface. The words "credits", "CR" and "vouchers" do not appear on screen as the name of the unit. Figures carry the token icon; sentences say "token" or "tokens" (§10).
- "Code" is the word for a voucher code ("Redeem code"), and "coffee ticket" for the QR a user shows at the bar.
- Say "market", "shares", "YES" and "NO" (capitals). Do not say "bet", "odds", "gamble" or "win money".
- Buttons are verbs: "Confirm", "Redeem code", "Get a coffee". Sentence case everywhere, no full stop on buttons or headings.

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
- No gradients, no glows, no coloured shadows.

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
- **Bean drop:** when tokens arrive (welcome grant, redeemed code, payout), three to five token icons fall from the top of the balance card into the figure over 600 ms, then the figure updates. It plays once per event, never on page load for an old event.
- **Bean bounce:** the trade receipt's token icon bounces once (200 ms).
- **Polybahn loader:** the progress line and its car (§3). It never blocks input.
- With `prefers-reduced-motion: reduce`, movement is removed: the bean drop, bean bounce and Polybahn car are not shown, the sheet and toasts cross-fade, the flash and the pulse are disabled.

## 8. Icons and imagery

- Icons are `lucide-react`, the set shadcn/ui ships with. Stroke 2 px; 20 px inline, 24 px in bars.
- An icon alone is never a control's only label, except the back arrow, the close cross and the tab bar, which all carry an `aria-label` (the tab bar also shows text).
- Icons are `--foreground` or `--muted-foreground`. An icon is green or red only inside an element that already is.
- Markets have no images and there is no photography. Illustration is pixel art only, from the short list in §3; empty states use one illustration (or a 32 px icon until it is drawn) and a sentence.
- The only raster images are the logo, the token icon, the pixel illustrations (§3) and generated QR codes.

## 9. Tokens

The stack is Tailwind CSS v4 with shadcn/ui components copied into `frontend/src/components/ui/`. shadcn/ui reads the standard variable names below; the MarkETH extras follow them. This block is the token section of `frontend/src/app/globals.css`.

```css
@import "tailwindcss";

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
```

- In shadcn/ui's vocabulary `--accent` is the hover surface, so it maps to `--raised`. The brand accent, white, is `--primary`.
- There is no `.dark` class and no `prefers-color-scheme` query: the dark values are the only values.
- `font-display` is the Tailwind class for Barlow; `font-sans` (the default) is Inter.

## 10. Showing data

The API sends display-ready values. The frontend may format them and must not derive new ones.

| Value | Arrives as | Shown as |
| --- | --- | --- |
| Probability (`p_yes`, `p_no`, `p_yes_after`) | Number in [0, 1], already rounded | Percentage with `price_display_decimals` decimals from `/config`: `54.8%` |
| Amount (`*_cr`) | Decimal string, trailing zeros trimmed | The token icon, then the string: (bean) `0.25`, (bean) `5` |
| Shares | Decimal string | The string, then "shares": `1.83 shares` |
| Average price (`average_price`) | Number in [0, 1] | (bean) `0.524` per share |
| Signed amount (ledger `amount_cr`) | Signed decimal string | Sign, token icon, figure: `+` (bean) `1` in `--yes-text`, `−` (bean) `0.25` in `--foreground` |
| Date and time | ISO 8601 | In the `/config` `timezone`, as `14 Oct 2026, 18:00` |
| Deadline in a list | ISO 8601 | Relative when under 7 days ("Closes in 3 days", "Closes today, 18:00"), otherwise the date |
| Code (voucher code) | 12 characters | `XXXX-XXXX-XXXX`, Barlow, letter-spacing 0.08em |
| Coffee ticket code | 8 characters | `XXXX-XXXX`, same style |

Rules:

- **Amounts are strings.** Never pass a `*_cr` or `shares` value through `parseFloat` or `Number`. Where a list needs a shorter figure, cut the string after the second decimal place (cutting, not rounding, so the app never shows more than the user has). The full string is shown on detail views: the trade receipt and the expanded ledger row.
- **Percentages** are formatted with `Intl.NumberFormat("en-GB", { style: "percent" })` and the `/config` decimals. `p_no` comes from the API; the frontend does not subtract.
- **Money out is not red.** A purchase lowering the balance is normal, so negative ledger amounts are white. Red is for losses and errors.
- **No derived figures.** There is no profit, loss, return or "potential winnings" figure, because the API sends none. A position shows `value_cr`, `cost_cr`, `proceeds_cr`, and after settlement `payout_cr` or `refund_cr`, each labelled.
- **No hard-coded tunables.** The stake limit, presets, welcome grant, ticket value, expiry days and allowed email domains come from `/config` or the market response. Copy is written with placeholders: "of {stake_limit_cr} tokens", never "of 1 token".
- **Icon or word.** A standalone figure (balance, row amount, quote value, chip) gets the token icon and no word. A figure inside a sentence gets the word and no icon: "You got 5 tokens." The plural follows the string: "1 token", otherwise "tokens".
- **Names in code are unchanged.** The API, the database and the spec call the unit a credit (`*_cr`, "CR"). "Token" is its name on screen only; do not rename fields.
- **Server text.** API messages and ledger labels are shown as sent, and some still say "vouchers" ("Not enough vouchers for this.", "Welcome vouchers"). Until the backend copy is changed to "tokens", that wording will appear in error messages and ledger rows.
- Locale is fixed to `en-GB` for numbers and dates. The minus sign is U+2212.

## 11. App shell and navigation

### Top bar

56 px tall, `--card`, sticky. Left: the logo with name (`logo-lockup.png`, 28 px tall, 16 px from the edge), linking to Markets. This is its fixed place on every main screen (Markets, Portfolio, Wallet, Admin), as on Polymarket; from 768 px the tabs follow it on the same line. Right: the balance as a pill (token icon and `balance_cr`, Barlow 600) linking to Wallet; signed out, a "Sign in" secondary button instead. On inner screens the left side is a back arrow and the screen title.

### Tab bar

64 px tall plus the bottom safe area, `--card`, 1 px `--border` on top, fixed. Each tab is an icon above a `caption` label. The active tab is `--foreground`; the others are `--muted-foreground`. There is no coloured indicator.

| Tab | Route | Shown to |
| --- | --- | --- |
| Markets | `/` | Everyone |
| Portfolio | `/portfolio` | Signed-in users |
| Wallet | `/wallet` | Signed-in users |
| Scan | `/barista` | Role BARISTA |
| Admin | `/admin` | Role ADMIN |

The role comes from `MeResponse.role`. Admins reach the scanner from the Admin screen. Signed-out visitors see Markets and a "Sign in" tab. The tab bar is hidden on Login, on the Barista scanner while the camera is live, and while the trade sheet is open.

### Routes

| Route | Screen | Fixed by |
| --- | --- | --- |
| `/` | Markets | this file |
| `/markets/[id]` | Market detail; the trade sheet opens over it | this file |
| `/portfolio` | Portfolio | this file |
| `/wallet` | Wallet; `#code=...` prefills the redeem field | SPEC A.6 |
| `/propose` | Propose market | this file |
| `/login` | Login, step 1 | this file |
| `/login/confirm` | Login, confirm step; the token is in the fragment | SPEC A.3 |
| `/admin` | Admin: proposal queue and markets | this file |
| `/admin/codes` | Admin: code batches | this file |
| `/admin/risk` | Admin: risk panel | SPEC §12 |
| `/barista` | Barista scanner | this file |

A signed-out visitor who opens a signed-in route is sent to `/login` and returned afterwards. A user without the role sees the standard "not found" screen, not a permission lecture.

## 12. Components

Each component lives in `frontend/src/components/` and builds on the shadcn/ui primitive named. All share these states: **default**, **pressed** (`--raised` or 90% opacity on fills), **focus-visible** (2 px `--ring` outline, 2 px offset), **disabled** (`--subtle` text, 50% fill, no pointer events) and, where it applies, **loading**.

### Button (shadcn `button`)

| Variant | Look | Use |
| --- | --- | --- |
| `primary` | `--primary` fill, `--primary-foreground` label | The one main action of a screen |
| `secondary` | Transparent, 1 px `--border-strong`, white label | Any other action |
| `ghost` | No outline, white label | Low-emphasis actions, "Cancel" |
| `destructive` | `--no` fill, `--ink` label | Reject, void, revoke. Always behind a confirmation dialog |
| `yes` / `no` | `--yes` or `--no` fill, `--ink` label | Outcome buttons only |

Height 48 px (56 px for the trade Confirm), `--radius-md`, `label` text style, full width on phones. A loading button keeps its width, shows a spinner in place of its label and is disabled.

### Outcome buttons and toggle

- **Outcome buttons** (Market detail): two equal buttons side by side, `yes` on the left and `no` on the right. Each shows the outcome and its price: "YES 54.8%". They open the trade sheet with that outcome selected.
- **Outcome toggle** (trade sheet, shadcn `toggle-group`): two segments. The selected one is filled (`--yes` or `--no`, `--ink` label); the other is `--background` with a `--muted-foreground` label. Each segment has `aria-pressed`.
- The outcome buttons and the selected toggle segment are the only full green and red fills in the trading flow. Everywhere else, Yes and No use the soft tints.

### Price

The probability figure. Sizes: `display` on Market detail, `number-lg` on cards. It shows the YES percentage in `--foreground` white with a `caption` `--muted-foreground` "chance" label beside it, as Polymarket does ("54.8% chance"). It turns green or red only for a resolved market (§ Status badge). It flashes on change (§7).

**Decision:** live probabilities are white, not green, as on Polymarket. Colour belongs to the Yes and No controls.

### Market card (shadcn `card`)

`--card`, `--radius-lg`, 1 px `--border`, 16 px padding, the whole card is one link. Top to bottom:

1. A row: the question (`h3`, up to three lines) on the left, the Price (`number-lg`, with "chance" under it) on the right.
2. Two soft outcome buttons side by side, 44 px tall: "Yes" (`--yes-soft` fill, `--yes-text` label) and "No" (`--no-soft` fill, `--no-text` label). Each opens the market with the trade sheet on that outcome. They are shown only when `trading_open` is true.
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

Plots `p_yes` from `/markets/{id}/history` over time.

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

`--card`, top corners `--radius-xl`, a 36 x 4 px `--border-strong` handle, `--scrim` behind. Maximum height 90% of the viewport; content scrolls inside while the action button stays pinned at the bottom above the safe area. Closes by drag, by tapping the scrim, or with the close button. From 768 px it is a `dialog`.

### Form fields (shadcn `input`, `textarea`, `label`)

- Label above the field (`caption`, `--muted-foreground`). Field 48 px tall, `--background` fill, 1 px `--border-strong`, `--radius-md`, 16 px text. Placeholder in `--muted-foreground`.
- Focus: the border becomes `--ring` and 2 px.
- Error: the border becomes `--no`, with the message below in `body-sm` `--no-text` next to an alert icon, linked by `aria-describedby`.
- Help text and character limits sit below the field in `caption`.
- The code fields use the code style (§10), `autocapitalize="characters"`, `autocorrect="off"`, and insert the dashes as the user types.

### List rows

56 px minimum height, 1 px `--border` divider, no outline of their own.

| Row | Left | Right |
| --- | --- | --- |
| Ledger | `description`, and `created_at` below in `caption` | Signed `amount_cr` |
| Trade | `market_question` (one line), and "Bought YES" or "Sold NO" with the date below | `amount_cr`, and `shares` below |
| Position | The question, and holdings below ("1.83 YES shares") | `value_cr`, with a status badge when settled |

### QR code and scanner

- **QR code:** always `--ink` modules on a `#FAFAFF` tile with 16 px padding and `--radius-lg`, at least 240 px wide. A dark-themed QR does not scan reliably. The code is repeated as text below it. Showing a ticket raises the screen brightness hint ("Turn up your brightness") instead of trying to control it.
- **Scanner:** a full-width camera view with a square guide in `--foreground` corners. It has a visible "Type the code instead" alternative, and a clear message when camera permission is refused. The camera needs HTTPS (SPEC A.10).

### Feedback

| Component | Based on | Use |
| --- | --- | --- |
| Toast | shadcn `sonner` | Confirmation of a finished action ("Trade done", "Code redeemed"). Bottom of the screen, above the tab bar, 4 s. `--raised` fill, white text |
| Banner | shadcn `alert` | A condition that lasts: a paused market, a 503. Full width inside the content column, with an icon. Neutral: `--raised`. Error: `--no-soft` with `--no-text` |
| Inline error | | Under the field or above the action button it concerns |
| Dialog | shadcn `alert-dialog` | Confirmation of destructive or irreversible admin actions only |
| Skeleton | shadcn `skeleton` | Loading. Shapes match the content they replace |
| Empty state | | One pixel illustration (§3), one sentence, and at most one button |

Errors are never shown only as a toast: a toast disappears, and the user must be able to read what went wrong.

## 13. Screens

Each screen lists its content from top to bottom. Field names are those of the API responses.

### Login (`/login`, `/login/confirm`)

No tab bar. Content centred vertically.

1. The logo with name, 48 px tall and centred, then one sentence: "Trade on campus questions with free tokens. One token, one coffee."
2. **Step 1:** an email field ("ETH email") and a primary "Send sign-in link" button. Help text lists `allowed_email_domains` from `/config`.
3. **Step 2,** after the 202: "Check your inbox" with the address, the validity from `link_ttl_minutes`, and a ghost "Use another address" button. The same screen appears whether or not the account exists (SPEC §3).
4. **Confirm page:** a single primary "Confirm sign-in" button. It never submits on load, because mail scanners open links (SPEC §3).
5. After a first sign-in (`signup_granted: true`), a welcome dialog: the 64 px token icon, "You got {signup_grant_cr} tokens", one sentence on how a share pays out, and "Start trading". The bean drop plays once.

States: a `LOGIN_LINK_INVALID` error on the confirm page offers "Request a new link". `RATE_LIMITED` shows the message and disables the button for `retry_after_seconds`. A 503 shows the banner.

### Markets (`/`)

1. Top bar.
2. Filter chips, horizontally scrollable: "Open" (default), "Closed", "Resolved", "All". They map to the `status` query.
3. Market cards, in the order the API returns them.
4. Signed in: a secondary "Propose a market" button after the list.

States: six skeleton cards while loading. Empty: the dome illustration and "No markets yet. Quieter than the HG on a Sunday." with, signed in, the propose button. Error: a banner with "Try again". The list is not live; it refetches on focus and on pull to refresh.

### Market detail (`/markets/[id]`)

1. Top bar with a back arrow.
2. The question (`h2`) and the status badge.
3. The Price in `display`, with "YES" beside it, and under it `volume_cr` and the deadline in `body-sm`.
4. The probability chart.
5. The outcome buttons (YES left, NO right). On phones they are pinned to the bottom of the screen above the tab bar once the user scrolls past them.
6. **Your position** (signed in, when holdings exist): each holding's `shares` and `value_cr`, then `net_stake_cr` and the stake-left line, and a secondary "Sell" button.
7. **Rules:** `yes_definition`, `no_definition`, `resolution_criteria`, `resolution_source`, the closing time `close_at` and `resolve_by`.
8. **If the market is voided:** the `void_rule` text, shown in full and not collapsed. SPEC §6 requires it to be visible before anyone trades.
9. **Resolution** (when resolved): the outcome badge, `evidence` and `resolved_at`.

States by status:

| Situation | What changes |
| --- | --- |
| Signed out (`viewer` is null) | The outcome buttons open Login. A line says "Sign in to trade." |
| `can_trade` false | The outcome buttons are disabled; a neutral banner explains `cannot_trade_reason`: "This market is not open for trading.", "You proposed this market, so you cannot trade it.", "Your account is suspended." |
| HALTED | A neutral banner "Trading is paused." Buttons disabled; the chart and position stay |
| CLOSED | Banner "Trading has closed. Waiting for the result." |
| RESOLVED | The Price is replaced by the outcome ("YES" in `--yes`, or "NO" in `--no`, `display` size). The position shows `payout_cr` |
| VOID | Banner "This market was voided. Stakes were refunded." The position shows `refund_cr` |

Live: the Price, chart and volume follow the stream (§14). A `status` event refetches the screen.

### Trade sheet

Opened from an outcome button or "Sell". Top to bottom:

1. The question, one line, `body-sm` `--muted-foreground`.
2. A **Buy / Sell** segmented control. "Sell" is shown only when the user holds shares.
3. The outcome toggle. When selling, only held outcomes are offered.
4. **Buying:** the stake presets and the stake-left line. **Selling:** a shares field with a "Sell all" chip that fills in the `shares` string of the holding exactly as the API sent it.
5. The quote panel.
6. A line with the balance: "Balance" and the token icon with `balance_cr`.
7. The Confirm button, 56 px. Its fill follows the outcome when buying ("Buy YES" on `--yes`, "Buy NO" on `--no`) and is `primary` white when selling ("Sell YES shares").

**Decision (selling):** SPEC §11 describes buying only, yet the acceptance criteria require selling. The API sizes a sale in shares and sends no sell presets, and the frontend may not compute amounts. So the user types a share quantity or taps "Sell all"; there is no "half" or percentage button, since that would be arithmetic on a holding. Every resulting number comes from the quote.

Behaviour:

- The quote is requested `quote_debounce_ms` (from `/config`) after the last change. Confirm is disabled until a quote for the current input has arrived.
- On Confirm the button shows its spinner and is disabled until the response arrives. One Idempotency-Key is created per attempt and reused for retries of that attempt (SPEC §11). A double tap cannot create two trades.
- The user pays the quoted cost, which can be slightly below the chosen preset. The panel's "You pay" is the truth, not the chip.
- **Success:** the sheet content is replaced by a receipt: a check icon, "Bought 1.83 YES shares", the amount, the new probability, the new balance and "Done". The market screen behind it updates from the response.
- **`QUOTE_STALE`:** the panel updates to the quote in `details.quote`, changed values flash, and an inline notice above the button reads "The price moved. Review the new quote and confirm again." The button returns to enabled. Nothing is submitted automatically.
- **Other errors:** shown inline above the button with the API's `message`; the sheet stays open and the input is kept. `LIMIT_EXCEEDED` also refreshes the stake-left line from `details`.

### Portfolio (`/portfolio`)

1. Top bar.
2. A summary card: "Value of open positions" with `total_value_cr` in `number-lg`.
3. Tabs: **Positions** and **History**.
4. Positions: position rows in API order (unsettled first), under the subheadings "Open" and "Settled". A settled row shows "Paid out" or "Refunded" with the token icon and `payout_cr` or `refund_cr`. A row opens the market.
5. History: trade rows from `/me/trades`, loading more with `next_cursor` as the user scrolls.

Empty: the empty-cup illustration and "No positions yet. Your cup is empty." with a primary "Browse markets" button.

### Wallet (`/wallet`)

1. Top bar.
2. Balance card: the 32 px token icon and `balance_cr` in `display`, labelled "Your tokens", and below it "Next monthly token: {next_monthly_grant_on}" with `monthly_grant_cr`.
3. Two buttons side by side: primary "Get a coffee", secondary "Redeem code".
4. **Coffee tickets:** active tickets as cards with the expiry date and a "Show QR" button; past tickets as muted rows ("Redeemed", "Expired, refunded").
5. **Activity:** ledger rows from `/me/ledger`, paged with `next_cursor`.

Flows:

- **Redeem code** opens a sheet with the code field, a "Scan QR" button and "Redeem". Opening `/wallet#code=...` opens this sheet with the field filled in; the user still taps "Redeem". Success closes the sheet with a toast "+{value_cr} tokens" and updates the balance. Every failure shows the same inline message from the API ("This code is not valid."); the screen never hints at why (SPEC §2).
- **Get a coffee** opens a confirmation sheet: "Turn {coffee_ticket_value_cr} token into a coffee ticket?" (plural by the string) with the expiry from `/config`. Confirming shows the ticket: the QR tile, the code, the expiry and "Show this at the bar". The code is returned once, so it is saved to local storage for "Show QR" later (SPEC A.6); a ticket whose code is no longer stored says so plainly and gives the expiry and refund date.
- **Decision:** "Get a coffee" is always enabled for a signed-in user. Whether the balance suffices is the server's call; `INSUFFICIENT_BALANCE` is shown inline in the sheet.

### Propose market (`/propose`)

A single-column form, one field per row, in this order: question, "YES means" (`yes_definition`), "NO means" (`no_definition`), how it is decided (`resolution_criteria`), source (`resolution_source`), trading closes (`close_at`), decided by (`resolve_by`), liquidity (`requested_tier`).

- Each text field shows its character limit. Date limits (`max_market_lifetime_days`, `max_resolution_delay_days`) are stated in help text from `/config`.
- The tier is three radio cards from `/config` `tiers`, each with a plain name ("Tight", "Default", "Medium") and one line of explanation.
- Above the submit button, a short list of the content rules of SPEC §6 (no private matters of named people, no grades, no outcomes one trader can decide, no party politics or religion) and the note "You cannot trade a market you propose."
- Primary "Submit for review". Validation errors from the API are placed on their fields using `details`.
- Below the form, **My proposals** from `/me/proposals`: the question, a status badge and, when rejected, `rejection_reason`.

### Admin (`/admin`, `/admin/codes`, `/admin/risk`)

Three tabs under the top bar: **Markets**, **Codes**, **Risk**. Designed for 1024 px; usable on a phone with horizontal scrolling tables. Density is higher here: rows are 44 px and `body-sm`.

- **Markets:** a "Proposals" queue first, then all markets filterable by status. A row opens an admin view of the market with `proposer_email` and an action bar. Actions by status: Approve (choose a tier, a budget for CUSTOM, optional edits) and Reject (reason) for PROPOSED; Halt and Close for OPEN; Resume and Close for HALTED; Resolve and Void for CLOSED (SPEC §6). Only the actions valid for the market's status are shown. Resolve asks for the outcome and evidence. Reject, Void, Resolve and Revoke are irreversible and open an `alert-dialog` that restates the consequence and requires the reason field.
- **Codes:** a table of batches (`label`, `value_cr`, `count`, `active_count`, `redeemed_count`, `revoked_count`, `expires_at`) and "New batch". After creation, the codes are shown **once** with "Download CSV" and "Print QR sheet", both generated in the browser (SPEC A.6), under a banner: "These codes cannot be shown again. Save them now." Leaving the page asks for confirmation. Nothing here names a price for a code.
- **Risk:** stat tiles for `obligations.total_cr` against `alert_threshold_cr`, `loss_budget_cr` of `max_loss_budget_cr`, `open_markets` of `max_open_markets`, `unredeemed_code_value_cr`, and code issuance this month against its limit. Then unacknowledged alerts with an "Acknowledge" button, then the per-market table. The obligations tile is outlined in `--no` with an alert icon when `above_threshold` is true. The other tiles show their figure and its limit side by side with no warning state, because the API sends no flag for them and the frontend does not compare figures itself. Alert severity: INFO and WARNING are neutral banners with different icons, CRITICAL uses the error banner.
- A "Scan coffee tickets" link leads to the Barista screen.

The print sheet is the one light surface in the product, because it is paper: `--ink` on unprinted paper, `logo-lockup-dark.png` at the top, a grid of QR codes with the code and value under each.

### Barista (`/barista`)

Built to be used at arm's length, fast, by someone holding a cup.

1. The scanner fills the screen. A "Type the code" button is below it.
2. A scan immediately shows a full-screen result for 2.5 s, then returns to the scanner:
   - **Accepted:** `--yes` background, `--ink` text, a large check icon, "Coffee ticket accepted" and `value_cr`.
   - **Rejected:** `--no` background, `--ink` text, a large cross icon and "Ticket not valid". No reason is given (SPEC A.6).
3. Each result also triggers a short vibration where supported: one pulse for accepted, two for rejected.

This is the only screen that uses a full-bleed green or red surface; the result must be readable from across a counter.

## 14. States and feedback

### Every screen has four states

Loading (skeletons), empty (icon, sentence, at most one button), error (banner with "Try again") and content. A screen is not finished until all four exist.

### Errors

The API returns `{code, message, details}`. The `message` is written for the user and is shown as sent; the frontend branches on `code` only to decide where and how to show it.

| Code | Where it appears | Treatment |
| --- | --- | --- |
| `QUOTE_STALE` | Trade sheet | New quote from `details.quote`, inline notice, confirm again |
| `INSUFFICIENT_BALANCE` | Trade sheet, coffee sheet | Inline above the button |
| `INSUFFICIENT_SHARES` | Trade sheet (sell) | Error on the shares field |
| `LIMIT_EXCEEDED` | Trade sheet, propose form, redeem sheet | Inline; on trades, refresh the stake-left line from `details` |
| `TRADE_TOO_SMALL` | Trade sheet | Error on the amount or shares field |
| `MARKET_NOT_OPEN` | Trade sheet | Inline, then refetch the market so the screen shows its real status |
| `CODE_INVALID` | Redeem sheet, barista | Inline on the field; the rejected screen on the scanner. Always the same wording |
| `RATE_LIMITED` | Login, redeem, trade | Inline; the action is disabled for `details.retry_after_seconds` with a countdown |
| `LOGIN_LINK_INVALID` | Confirm page | Message with a "Request a new link" button |
| `VALIDATION_ERROR` | Forms | On the field named in `details`; otherwise above the submit button |
| `UNAUTHENTICATED` | Anywhere | Go to Login, then return. No error styling: the session simply ended |
| `FORBIDDEN` | Anywhere | The API's message as a banner |
| `NOT_FOUND` | Routes | A "not found" screen with a link to Markets |
| `CONFLICT` | Admin actions | Inline in the dialog, then refetch |
| `IDEMPOTENCY_KEY_REUSED` | Trade sheet | Should not occur. Show the generic error and create a new key for the next attempt |
| `SERVICE_UNAVAILABLE` | Anywhere | Banner "This is temporarily unavailable. Try again shortly." Login and code redemption need Redis; trading does not |
| `INTERNAL_ERROR` | Anywhere | Banner with the message and "Try again" |

A network failure with no response uses the same banner: "No connection. Try again." A trade that got no response is retried with the same Idempotency-Key, so it cannot execute twice.

### Live data

- Market detail subscribes to `/markets/{id}/stream`. A `quote` event updates the Price, the chart and the volume; a `status` event refetches the market.
- Events are hints, not truth: one whose `version` is not newer than what is on screen is dropped.
- If the stream ends or answers 503, the screen polls every `live_poll_fallback_seconds` from `/config` and keeps working. There is no error banner for this; the user sees slightly slower updates.
- An open quote in the trade sheet is not replaced by a stream event. The user confirms what they were shown, and the server answers `QUOTE_STALE` if it has moved.
- Balances can change between visits (monthly grants and ticket refunds are booked lazily, SPEC A.3), so the balance is refetched whenever Wallet or the trade sheet opens.

### Buttons that change state

Every button that posts is disabled from tap until response. Destructive admin actions additionally go through a confirmation dialog. Nothing in the product is submitted by a page load, a scan or a prefilled field alone; there is always a deliberate tap.

## 15. Accessibility

- **Contrast:** text meets WCAG AA using the pairs in §4. A new colour pair is measured before it is used.
- **Colour blindness:** green and red always come with a word, a sign or an icon. The outcome buttons keep a fixed order, YES on the left.
- **Focus:** a 2 px `--ring` outline on `:focus-visible` for every interactive element. Sheets and dialogs trap focus and return it to the opener on close.
- **Semantics:** one `h1` per screen; the tab bar is a `nav`; the outcome toggle uses `aria-pressed`; form errors use `aria-describedby` and `aria-invalid`.
- **Live regions:** the live Price is in an `aria-live="polite"` region limited to one announcement per 10 s. The `QUOTE_STALE` notice and trade errors use `role="alert"`.
- **Screen-reader text for figures:** the Price reads "YES 54.8 percent". An amount reads "0.25 tokens".
- **Chart:** an `aria-label` summarises it ("YES probability over time, now 54.8 percent, opened at 50 percent"), and the same information is always present as text on the screen.
- **Text size:** layouts hold at 200% browser zoom and with large system text; type is set in `rem`.
- **Motion:** `prefers-reduced-motion` is honoured (§7).
- **Camera:** every scan has a typed alternative.

## 16. Review checklist

Do:

- Use tokens and the listed components; add a component here before adding it in code.
- Show the API's strings and messages as sent.
- Give every screen its loading, empty and error state.
- Keep one white primary button per screen.
- Check each new screen at 375 px, at 200% zoom and with the keyboard.

Don't:

- Compute, compare or round a price, balance, limit or share quantity in the frontend.
- Hard-code a tunable ("1 token", "5 tokens", "30 days") in copy or logic.
- Use green or red for anything that is not YES, NO, a gain, a loss, an error or a destructive action.
- Put white text on a green or red fill, or pure red text on a card.
- Add a light theme, a gradient, a shadow on a card, or a remote image or font.
- Use the ETH logo, or type the name beside the logo with name.
- Use pure white `#FFFFFF` or pure black `#000000` anywhere on screen.
- Put a joke in an error, a balance, the trade sheet or the admin area, or more than one playful element on a screen.
- Add any screen, button or wording about buying tokens or codes.
- Show "CR", "credits" or "vouchers" as the unit's name, or the bean without its white disc.
- Say why a code or coffee ticket was refused, or whether an email address has an account.
