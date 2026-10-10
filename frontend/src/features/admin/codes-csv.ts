import type { CodeBatchCreatedResponse } from "@/lib/api/schema";

/**
 * The codes of a batch as a CSV file, made in the browser from the one answer that holds
 * them (DESIGN.md §13, SPEC A.6). One row per code: the code as printed on its card, the
 * link its QR holds, and what the batch says about every code of it. Every value is
 * written as the API sent it.
 */

const COLUMNS = ["code", "redeem_url", "label", "tokens", "expires_at"] as const;

/** A spreadsheet reads a cell that starts like this as a formula. */
const FORMULA_START = /^[=+\-@\t\r]/;

/** RFC 4180: a cell with a comma, a quote or a line break is quoted, its quotes doubled. */
function cell(value: string): string {
  return /[",\r\n]/.test(value) ? `"${value.replaceAll('"', '""')}"` : value;
}

/** The label is the one cell a person typed: it must stay text in a spreadsheet. */
function text(value: string): string {
  return FORMULA_START.test(value) ? `'${value}` : value;
}

export function codesCsv({ batch, codes }: CodeBatchCreatedResponse): string {
  const rows = codes.map(({ code, redeem_url }) => [
    code,
    redeem_url,
    text(batch.label),
    batch.value_cr,
    batch.expires_at,
  ]);
  return [COLUMNS, ...rows].map((row) => row.map(cell).join(",") + "\r\n").join("");
}

/** `codes-welcome-week.csv` for a batch labelled "Welcome week". */
export function csvFileName(label: string): string {
  const slug = label
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return `codes-${slug === "" ? "batch" : slug}.csv`;
}
