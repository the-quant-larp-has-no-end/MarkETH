/**
 * Propose market (`/propose`). The screen is what the route renders; the rest is what the
 * approval of a proposal in the admin area shares with the form: the same seven fields
 * of a market's wording and times.
 */
export {
  type MarketTerms,
  MarketTermsFields,
  statedTerms,
  type StatedTerms,
  TERM_FIELDS,
  type TermField,
} from "./market-terms-fields";
export { ProposeScreen } from "./propose-screen";
