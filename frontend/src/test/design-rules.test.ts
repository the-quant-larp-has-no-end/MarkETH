import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";

import { describe, expect, test } from "vitest";

/**
 * The rules of DESIGN.md and of the root CLAUDE.md that can be read off the source text.
 * They hold for every file under `src/`: the foundation, and every screen built on it.
 * A failure names the file, the line and the rule; the fix is in the code, not here.
 */

const ROOT = process.cwd();
const SRC = join(ROOT, "src");

type Source = { path: string; lines: string[] };

function walk(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    return entry.isDirectory() ? walk(path) : [path];
  });
}

const isTest = (path: string) => /\.test\.tsx?$/.test(path) || path.startsWith("test/");

const sources: Source[] = walk(SRC)
  .map((absolute) => ({ absolute, path: relative(SRC, absolute).split("\\").join("/") }))
  .filter(({ path }) => /\.(ts|tsx|css)$/.test(path) && path !== "lib/api/schema.d.ts")
  .map(({ absolute, path }) => ({ path, lines: readFileSync(absolute, "utf8").split("\n") }));

/** The code of the app itself: what ships, without the tests and their helpers. */
const app = sources.filter((source) => !isTest(source.path));
const code = app.filter((source) => !source.path.endsWith(".css"));

/** A line that is a comment says what the code does; it is not what the code does. */
const isComment = (line: string) => /^\s*(\/\/|\/?\*)/.test(line);

type Rule = {
  /** What is wrong with a line that matches. */
  why: string;
  pattern: RegExp;
  /** Files the rule does not apply to, each for the reason given beside it. */
  except?: readonly string[];
  /** Also look inside comments. */
  comments?: boolean;
};

function violations(files: readonly Source[], rule: Rule): string[] {
  return files
    .filter((file) => !rule.except?.includes(file.path))
    .flatMap((file) =>
      file.lines.flatMap((line, index) =>
        (rule.comments || !isComment(line)) && rule.pattern.test(line)
          ? [`${file.path}:${index + 1}  ${line.trim()}`]
          : [],
      ),
    );
}

function check(files: readonly Source[], rule: Rule): void {
  expect(violations(files, rule), rule.why).toEqual([]);
}

test("there is source to check", () => {
  expect(code.length).toBeGreaterThan(50);
  expect(sources.some((source) => source.path === "app/globals.css")).toBe(true);
});

describe("an amount is a string and stays one (DESIGN.md §10, golden rule 6)", () => {
  const AMOUNT = String.raw`(\w*_cr|shares|min_shares|magnitude|amount|balance)`;

  test("nothing calls parseFloat", () => {
    check(sources, {
      why: "parseFloat turns a string into a float; amounts are shown as the API sent them.",
      pattern: /\bparseFloat\b/,
      except: ["test/design-rules.test.ts"],
    });
  });

  test("nothing turns an amount into a number", () => {
    check(sources, {
      why: "Number(), parseInt() and Math.* on a *_cr or shares value compute with an amount.",
      pattern: new RegExp(
        String.raw`\b(Number|Number\.parseInt|parseInt|Math\.\w+|BigInt)\(\s*[^()]*\b${AMOUNT}\b`,
      ),
      except: ["test/design-rules.test.ts"],
    });
  });

  test("nothing compares or calculates with an amount", () => {
    check(code, {
      why: "An amount is never ordered, added or measured: the API sends allowed, affordable, can_trade.",
      pattern:
        /\b\w*_cr\s*(<=?|>=?|[-*/]|\+(?!\+))\s*[\w("'`]|[\w)"'`]\s*(<=?|>=?|[-*/])\s*[\w.]*\b\w+_cr\b(?!["'`\w])/,
    });
  });
});

describe("only tokens and listed components (DESIGN.md §1, §4, §9)", () => {
  test("no raw colour outside the token file", () => {
    check(app, {
      why: "A component uses the classes the tokens generate (bg-card, text-yes-text), never a colour of its own.",
      pattern:
        /#[0-9a-fA-F]{3}([0-9a-fA-F]([0-9a-fA-F]{2}([0-9a-fA-F]{2})?)?)?\b|\b(rgba?|hsla?|oklch|oklab|lab|lch|color-mix)\(/,
      except: [
        "app/globals.css", // the tokens themselves
        "app/layout.tsx", // theme-color: a meta tag cannot read a CSS variable
      ],
    });
  });

  test("no pure white or black, and no palette that is not ours", () => {
    check(code, {
      why: "White is --foreground and black is --ink; the palette is the five colours of DESIGN.md §4.",
      pattern:
        /\b(text|bg|border|ring|outline|fill|stroke|shadow|divide|from|via|to)-(white|black|slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)\b/,
    });
  });

  test("no light theme and no theme switch", () => {
    check(code, {
      why: "Dark theme only: the dark values are the only values.",
      pattern: /\bdark:|prefers-color-scheme|next-themes/,
    });
  });

  test("no gradient, no glass, no blur", () => {
    check(app, {
      why: "Flat fills and opaque surfaces only (DESIGN.md §1, What MarkETH must not look like).",
      pattern:
        /\bbg-(linear|gradient|radial|conic)-|\b(linear|radial|conic)-gradient\(|\bbackdrop-|backdrop-filter|\bblur-(xs|sm|md|lg|xl|2xl|3xl)\b|filter:\s*blur/,
    });
  });

  test("no shadow except under the bottom sheet and the dialogs", () => {
    check(code, {
      why: "Depth comes from surface colour; the only shadow of the system is under sheets and dialogs.",
      pattern: /\b(drop-)?shadow-(?!none\b)[a-z0-9[(]|box-shadow|boxShadow:\s*"(?!none")/,
      except: ["components/ui/dialog.tsx", "components/ui/drawer.tsx"],
    });
  });

  test("type is set with the type-* utilities, in rem", () => {
    check(code, {
      why: "The ten styles of DESIGN.md §5 are type-display to type-caption; no size of a component's own.",
      pattern:
        /\btext-(xs|sm|base|lg|xl|[2-9]xl)\b|\btext-\[[\d.]+(px|rem|em)\]|font-size:|fontSize:\s*["'`]?[\d.]+px/,
      except: [
        // sonner styles its toasts itself and wins over a class: the size of body-sm, in rem.
        "components/ui/sonner.tsx",
      ],
    });
  });

  test("radii are the four of the system, or full", () => {
    check(code, {
      why: "DESIGN.md §6: --radius-sm, -md, -lg, -xl and rounded-full. No ad-hoc or oversized radius.",
      pattern: /\brounded(-[trblse]{1,2})?-(\[|[2-9]xl\b|xs\b)|border-radius:|borderRadius:/,
    });
  });
});

describe("one door to the API, and nothing kept in the browser", () => {
  test("only the client calls fetch", () => {
    check(code, {
      why: "Every call goes through call() in lib/api/client.ts: one place for Accept, errors and the portal's 401.",
      pattern: /(^|[^.\w])fetch\(|\bXMLHttpRequest\b|\baxios\b|navigator\.sendBeacon/,
      except: ["lib/api/client.ts"],
    });
  });

  test("every address of the API is relative to the page's own origin", () => {
    check(code, {
      why: "The API is /api on the same origin; no host, no port, no environment.",
      pattern: /["'`]https?:\/\/|process\.env|NEXT_PUBLIC_/,
    });
  });

  test("no server code talks to the API", () => {
    check(code, {
      why: "The web server has no identity: no route handler, no server action, no server-side fetch.",
      pattern:
        /["']use server["']|next\/headers|next\/server|NextRequest|NextResponse|getServerSideProps/,
    });
    expect(sources.filter((source) => /(^|\/)route\.ts$/.test(source.path))).toEqual([]);
  });

  test("nothing is written to browser storage", () => {
    check(code, {
      why: "A code, a voucher code and an unanswered trade live in memory (DESIGN.md §13, §14).",
      pattern: /\b(localStorage|sessionStorage|indexedDB|document\.cookie)\b/,
      except: [
        // The one flag that says the page reloaded once for the portal's 401.
        "lib/session-recovery.ts",
      ],
    });
  });
});

describe("a second submit is stopped where it is made (DESIGN.md §14)", () => {
  /** Everything that puts something on a screen: the layers above `lib`, tests included. */
  const screens = sources.filter((source) => /^(app|components|features)\//.test(source.path));

  test("a write is sent through useSubmit or a kept submission, never through useMutation", () => {
    expect(screens.length).toBeGreaterThan(50);
    check(screens, {
      why: "useMutation stops no second submit: a write goes through useSubmit (lib/api/submit.ts) or useSubmissions (lib/api/submissions.ts).",
      pattern: /\buseMutation\s*(<.*>)?\s*\(/,
      except: [
        // The one write no tap sends. Each notice is its own request, sent once as the
        // notice is told, and what was told is remembered for the page: a guard of one
        // request at a time would hold back the second of two notices.
        "features/session/notices.tsx",
      ],
    });
  });
});

describe("words (DESIGN.md §3, Voice)", () => {
  const SENTENCE = String.raw`[^"'\`<>{}]*`;

  test("the unit is called tokens on screen, never credits or CR", () => {
    check(code, {
      why: "'credit' and 'CR' are names in the API and the database; on screen the unit is the token.",
      pattern: new RegExp(
        [
          // text between two tags
          String.raw`>${SENTENCE}\b(CR|[Cc]redits?)\b${SENTENCE}<`,
          // a sentence in a string: the word with a space on either side of it
          String.raw`["'\`]${SENTENCE}(\s\b(CR|[Cc]redits?)\b|\b(CR|[Cc]redits?)\b\s)${SENTENCE}["'\`]`,
        ].join("|"),
      ),
    });
  });

  test("nobody bets, gambles or wins money, and nothing is for sale", () => {
    check(code, {
      why: "Say market, shares, YES and NO; tokens are given, a code is redeemed, a voucher is got.",
      pattern:
        /["'`>][^"'`<>{}]*\b(bets?|betting|odds|gambl\w+|win money|top up|deposit|cash out|checkout|purchase)\b[^"'`<>{}]*["'`<]/i,
    });
  });

  test("code and comments describe what is, not what was", () => {
    check(sources, {
      why: "No 'legacy', 'v2', 'deprecated', 'formerly', 'previously' or 'no longer': say what is.",
      pattern: /\b(legacy|v2|deprecated|formerly|previously|no longer)\b/i,
      comments: true,
      except: ["test/design-rules.test.ts"],
    });
  });
});

// DESIGN.md lies beside src/ in the repository; an image is built without it.
const DESIGN = join(ROOT, "DESIGN.md");

describe.skipIf(!existsSync(DESIGN))("globals.css is the token section of DESIGN.md", () => {
  test("the block of DESIGN.md §9 stands in globals.css, character for character", () => {
    const design = readFileSync(DESIGN, "utf8");
    const block = /```css\n(@import "tailwindcss";[\s\S]*?)```/.exec(design)?.[1];
    const globals = readFileSync(join(SRC, "app/globals.css"), "utf8");

    expect(
      block,
      "DESIGN.md §9 has a css block that starts with the Tailwind import",
    ).toBeDefined();
    expect(globals).toContain(block!.trim());
  });

  test("the colours of §9 are the only colours globals.css defines", () => {
    const globals = sources.find((source) => source.path === "app/globals.css")!;
    const design = readFileSync(DESIGN, "utf8");
    const block = /```css\n(@import "tailwindcss";[\s\S]*?)```/.exec(design)![1]!;
    const colour = /#[0-9a-fA-F]{6}\b|rgb\([^)]*\)/g;

    const defined = new Set(globals.lines.join("\n").match(colour));
    const allowed = new Set(block.match(colour));

    expect([...defined].filter((value) => !allowed.has(value))).toEqual([]);
  });
});
