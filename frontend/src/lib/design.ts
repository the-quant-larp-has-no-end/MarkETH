/**
 * The numbers DESIGN.md fixes that code has to know. They are design constants, each
 * with the section that sets it. A tunable of the product is never here: that comes
 * from `GET /api/config` (`useConfig()`).
 */

/** §12, Feedback: a toast stays for 4 s. */
export const TOAST_MS = 4000;

/** §13, Barista: the full-screen result shows for 2.5 s, then the scanner returns. */
export const BARISTA_RESULT_MS = 2500;

/** §13, Users: the search asks 300 ms after the last keystroke. */
export const SEARCH_DELAY_MS = 300;

/**
 * §12, QR code: the quiet zone a camera needs around a code, in the code's own modules.
 * It is counted from the size a module is drawn at, so it holds at every width.
 */
export const QR_QUIET_MODULES = 4;

/** §15, Live regions: the live price is announced at most once per 10 s. */
export const PRICE_ANNOUNCE_MS = 10_000;

/** §7, Bean drop: the beans fall for 600 ms, then the figure updates. */
export const BEAN_DROP_MS = 600;

/** §7, Bean drop: three to five token icons. */
export const BEAN_DROP_COUNT = 5;

/** §6, Breakpoints: from here the bottom sheet is a dialog and the tabs sit in the top bar. */
export const WIDE_QUERY = "(min-width: 768px)";

/** §6, Breakpoints: from here Market detail shows the trade ticket as a sticky panel. */
export const DESKTOP_QUERY = "(min-width: 1024px)";

/** §7: movement is removed for a visitor who asks for less of it. */
export const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)";
