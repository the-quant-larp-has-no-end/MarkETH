import { afterEach, describe, expect, test, vi } from "vitest";

import { codeBatch, codeBatchCreated } from "@/test/fixtures";

import { browser } from "./browser";
import { codesCsv, csvFileName } from "./codes-csv";

describe("the CSV of a new batch", () => {
  test("has one row per code with its link, and the batch's label, value and expiry as the API sent them", () => {
    const csv = codesCsv(
      codeBatchCreated({
        batch: codeBatch({
          label: "Welcome week",
          value_cr: "12.5",
          expires_at: "2026-12-31T22:59:00Z",
        }),
        codes: [
          { code: "4F7K-Q2M9-XAB3", redeem_url: "https://example.org/wallet?code=4F7K-Q2M9-XAB3" },
          { code: "T8WN-3CB4-Z6PD", redeem_url: "https://example.org/wallet?code=T8WN-3CB4-Z6PD" },
        ],
      }),
    );

    expect(csv).toBe(
      [
        "code,redeem_url,label,tokens,expires_at",
        "4F7K-Q2M9-XAB3,https://example.org/wallet?code=4F7K-Q2M9-XAB3,Welcome week,12.5,2026-12-31T22:59:00Z",
        "T8WN-3CB4-Z6PD,https://example.org/wallet?code=T8WN-3CB4-Z6PD,Welcome week,12.5,2026-12-31T22:59:00Z",
        "",
      ].join("\r\n"),
    );
  });

  test("a label with a comma, a quote or a line break stays one cell", () => {
    const csv = codesCsv(
      codeBatchCreated({
        batch: codeBatch({ label: 'VIS "Katerbrunch", HG\nfoyer' }),
        codes: [{ code: "4F7K-Q2M9-XAB3", redeem_url: "https://example.org/w?code=4F7K" }],
      }),
    );

    expect(csv.split("\r\n")[1]).toBe(
      '4F7K-Q2M9-XAB3,https://example.org/w?code=4F7K,"VIS ""Katerbrunch"", HG\nfoyer",35,2026-12-31T22:59:00Z',
    );
  });

  test.each(["=1+1", "+41", "-5 for all", "@vis"])(
    "the label %j stays text in a spreadsheet",
    (label) => {
      const csv = codesCsv(
        codeBatchCreated({
          batch: codeBatch({ label }),
          codes: [{ code: "4F7K-Q2M9-XAB3", redeem_url: "https://example.org/w" }],
        }),
      );

      expect(csv.split("\r\n")[1]?.split(",")[2]).toBe(`'${label}`);
    },
  );

  test("a batch without codes is the heading row alone", () => {
    expect(codesCsv(codeBatchCreated({ codes: [] }))).toBe(
      "code,redeem_url,label,tokens,expires_at\r\n",
    );
  });

  test("the file is named after the batch", () => {
    expect(csvFileName("Welcome week")).toBe("codes-welcome-week.csv");
    expect(csvFileName("  VIS: Katerbrunch (HS26)!  ")).toBe("codes-vis-katerbrunch-hs26.csv");
    expect(csvFileName("???")).toBe("codes-batch.csv");
  });
});

describe("what the Codes screen asks of the browser", () => {
  const original = {
    createObjectURL: URL.createObjectURL,
    revokeObjectURL: URL.revokeObjectURL,
    print: window.print,
  };

  afterEach(() => {
    URL.createObjectURL = original.createObjectURL;
    URL.revokeObjectURL = original.revokeObjectURL;
    window.print = original.print;
  });

  test("a file is made in the page and offered under its name; it is let go of when its maker says so", () => {
    const made: Blob[] = [];
    URL.createObjectURL = vi.fn((blob: Blob | MediaSource) => {
      made.push(blob as Blob);
      return "blob:http://localhost:3000/the-file";
    });
    URL.revokeObjectURL = vi.fn();
    const clicked: { href: string; download: string; attached: boolean }[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      clicked.push({ href: this.href, download: this.download, attached: this.isConnected });
    });

    const release = browser.saveFile(
      "codes-welcome-week.csv",
      "code\r\n4F7K\r\n",
      "text/csv;charset=utf-8",
    );

    expect(clicked).toEqual([
      {
        href: "blob:http://localhost:3000/the-file",
        download: "codes-welcome-week.csv",
        attached: true,
      },
    ]);
    expect(made).toHaveLength(1);
    expect(made[0]?.type).toBe("text/csv;charset=utf-8");
    expect(made[0]?.size).toBe("code\r\n4F7K\r\n".length);
    // Nothing of it stays in the page but the file itself, until it is let go of.
    expect(document.querySelector("a[download]")).toBeNull();
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();

    release();

    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:http://localhost:3000/the-file");
  });

  test("printing opens the browser's own print dialog", () => {
    const print = vi.fn();
    window.print = print;

    browser.print();

    expect(print).toHaveBeenCalledTimes(1);
  });
});
