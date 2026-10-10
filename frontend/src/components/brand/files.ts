import type { StaticImageData } from "next/image";

import logoLockupDark from "../../../public/brand/logo-lockup-dark.png";
import logoLockup from "../../../public/brand/logo-lockup.png";
import logoMark from "../../../public/brand/logo-mark.png";
import token from "../../../public/brand/token.png";
import trophy1 from "../../../public/brand/trophy-1.png";
import trophy2 from "../../../public/brand/trophy-2.png";
import trophy3 from "../../../public/brand/trophy-3.png";

/**
 * The brand images a component shows, each under the name of its file in `public/brand`.
 *
 * A file is imported here and never named by its path. The build then shows it from an
 * address that carries a hash of its content, and the web app answers such an address as
 * immutable: a browser keeps the file without asking again, and a changed file has
 * another address by the next page load after a deployment (DESIGN.md §8).
 */
export const BRAND_FILES = {
  "logo-lockup": logoLockup,
  "logo-lockup-dark": logoLockupDark,
  "logo-mark": logoMark,
  token,
  "trophy-1": trophy1,
  "trophy-2": trophy2,
  "trophy-3": trophy3,
} as const satisfies Record<string, StaticImageData>;

export type BrandFile = keyof typeof BRAND_FILES;
