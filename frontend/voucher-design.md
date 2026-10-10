# MarkETH Code Voucher Design

A focused visual addendum to the existing `DESIGN.md`. Read that file first; keep its tokens, typography, components, behaviour, and accessibility rules. This file refines the **admin-generated code / QR print sheet** shown in the reference, plus its redeem-code presentation. It does not define a new design system.

## Direction

Make each code feel like a considered MarkETH campus token slip: crisp, compact, orderly, and recognisably part of the same flat, dark trading product. Improve hierarchy and alignment, not decoration. Use Barlow for amounts and codes, Inter for labels, and only the existing palette and assets.

**Use the product vocabulary correctly:** a **code** is redeemed for tokens; a **voucher** is the QR used at the bar for a product. The attached batch sheet is a code sheet. Do not label it “top-up credits” or imply that tokens are sold.

## In-app redemption

- Keep the existing Wallet flow: a code field, **Scan QR**, and one primary **Redeem** action. Scanning fills the field; the user still confirms by tapping Redeem. Do not invent a new promo-code card, checkout flow, or “top up” language.
- Keep the app dark: `--background` canvas, `--card` surfaces, 1 px `--border` dividers, and existing radii. No gradients, glow, coloured shadows, decorative patterns, or generic coupon graphics.
- Show the code in the existing grouped, letter-spaced Barlow code style; never truncate or hide the manual-entry alternative. Keep errors inline and use the API message verbatim.
- If an in-app QR is displayed, reuse the QR treatment already defined in `DESIGN.md`: `--ink` modules on a `#FAFAFF` tile, 16 px padding, `--radius-lg`, at least 240 px wide, with a clear quiet zone. Never invert or tint it.
- Use only API-provided values and existing states. Do not invent balances, expiry information, or success states.

## Printable code sheet

- A4 portrait, 12 mm margins. The print stylesheet contains only the sheet—no app header, tabs, dialogs or browser UI.
- The page is paper: `--ink` on unprinted paper. The cards on it are the app on paper, in the palette defined at the start: `--card` navy surfaces, `--foreground` text, `--raised` for the value pill, `--muted-foreground` for metadata. The sheet sets `print-color-adjust: exact` so the fills print without the “Background graphics” option.
- Header: `logo-lockup-dark.png` at 10 mm high on the left; batch label and “Valid until” date/time aligned on the right. One fine ink rule below the header separates it from the cards.
- Use a fixed **three-column grid**: 58 mm per card, 6 mm gutters, in API order. Do not stretch two cards across the page when a batch has fewer than three codes.
- A card is a `--card` surface with `--radius-lg`, built from the app’s own components. Top line, as the app’s top bar: `logo-lockup.png` on the left and `value_cr` in the balance pill (bean on its disc and the figure) on the right. Then a centred 40 mm QR for `redeem_url` on the `#FAFAFF` tile of `DESIGN.md`, then the full code in Barlow code style, then “Valid until” small and muted. A card that is cut out still says what it is.
- Use thin dashed ink hairlines only as cut guides between cards; no outlines, shadows, gradients or ornamental ticket notches. Keep card spacing generous enough to cut and handle easily.
- Never split a card or row across pages. Preserve a four-module QR quiet zone inside the light tile, and never invert or tint the QR. Verify the result in print preview and on an actual-size A4 export.

## Avoid

No new colours, fonts, icon sets, illustrations, gradients, glass effects, marketing copy, invented data, or changes to redemption logic. The result should look like a refined MarkETH utility—not a SaaS promo, gift-card storefront, or casino coupon.
