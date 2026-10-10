import { fireEvent, render, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";
import { describe, expect, test } from "vitest";

import { BRAND_FILES, type BrandFile } from "./files";
import { Illustration, Logo, LogoMark, TokenIcon, Trophy } from "./index";
import { sessionCopy } from "./session-copy";

/**
 * What "a brand image is fetched once per page session" (DESIGN.md §8) relies on in the
 * `img` of every brand component. The browser suite counts the requests
 * (`e2e/images.spec.ts`); this file holds the attributes.
 */

/** The address a file is imported under: a string here, an object with `src` in a build. */
function addressOf(name: BrandFile): string {
  const file: unknown = BRAND_FILES[name];
  return typeof file === "string" ? file : (file as { src: string }).src;
}

const brandImages = () => [...document.querySelectorAll<HTMLImageElement>("img[data-brand]")];

function brandImage(): HTMLImageElement {
  const found = brandImages();
  expect(found).toHaveLength(1);
  return found[0];
}

/** What the browser asks for when it shows `img`. */
const shownFrom = (img: HTMLImageElement) => img.currentSrc || img.src;

const SHOWN: [string, ReactElement, BrandFile][] = [
  ["TokenIcon", <TokenIcon key="token" />, "token"],
  ["Logo", <Logo key="logo" />, "logo-lockup"],
  ["Logo on paper", <Logo key="paper" on="paper" />, "logo-lockup-dark"],
  ["LogoMark", <LogoMark key="mark" />, "logo-mark"],
  ["Trophy 1", <Trophy key="1" place={1} size={96} eager />, "trophy-1"],
  ["Trophy 2", <Trophy key="2" place={2} size={72} eager />, "trophy-2"],
  ["Trophy 3", <Trophy key="3" place={3} size={24} />, "trophy-3"],
];

describe("a brand image", () => {
  test.each(SHOWN)("%s marks its img and names the file it shows", (_name, element, file) => {
    render(element);

    expect(brandImage()).toHaveAttribute("data-brand", file);
  });

  test.each(SHOWN)(
    "%s shows the file under the address it is imported with",
    (_n, element, file) => {
      render(element);

      expect(decodeURIComponent(brandImage().getAttribute("src")!)).toContain(addressOf(file));
    },
  );

  test.each(SHOWN)("%s is fetched when it mounts and decoded with its frame", (_n, element) => {
    render(element);

    expect(brandImage()).toHaveAttribute("loading", "eager");
    expect(brandImage()).toHaveAttribute("decoding", "sync");
  });

  test("every file of the list is a file of public/brand with the name it is listed under", () => {
    for (const name of Object.keys(BRAND_FILES) as BrandFile[]) {
      expect(addressOf(name)).toMatch(new RegExp(`/brand/${name}\\.png$`));
    }
  });

  test("every file of the list is shown by a component", () => {
    expect(SHOWN.map(([, , file]) => file).sort()).toEqual(Object.keys(BRAND_FILES).sort());
  });

  test("the trophies of the podium are fetched ahead of the other images", () => {
    render(
      <>
        <Trophy place={1} size={96} eager />
        <Trophy place={3} size={24} />
      </>,
    );

    const [podium, row] = brandImages();
    expect(podium).toHaveAttribute("fetchpriority", "high");
    expect(row).not.toHaveAttribute("fetchpriority");
  });

  test("an illustration slot without a drawing shows no brand image", () => {
    render(<Illustration name="empty-cup" />);

    expect(brandImages()).toHaveLength(0);
  });
});

describe("the copy the page keeps", () => {
  test("is made when the image has loaded, as an img that is not on the page", async () => {
    render(<LogoMark />);
    const address = shownFrom(brandImage());
    expect(sessionCopy(address)).toBeUndefined();

    fireEvent.load(brandImage());

    await waitFor(() => expect(sessionCopy(address)).toBeDefined());
    const copy = sessionCopy(address)!;
    expect(copy.tagName).toBe("IMG");
    expect(copy.src).toBe(address);
    expect(copy).toHaveAttribute("decoding", "sync");
    expect(copy.isConnected).toBe(false);
  });

  test("is one per image, however many elements show it and however often they mount", async () => {
    const first = render(<TokenIcon />);
    const address = shownFrom(brandImage());
    fireEvent.load(brandImage());
    await waitFor(() => expect(sessionCopy(address)).toBeDefined());
    const copy = sessionCopy(address);
    first.unmount();

    render(
      <>
        <TokenIcon size={20} />
        <TokenIcon size={64} />
      </>,
    );
    for (const img of brandImages()) fireEvent.load(img);

    // The second load is told a moment after the event, as the first one was.
    await waitFor(() => expect(sessionCopy(shownFrom(brandImages()[1]))).toBeDefined());
    expect(sessionCopy(address)).toBe(copy);
  });

  test("is not made for an image that has not loaded", () => {
    render(<Logo on="paper" />);

    expect(sessionCopy(shownFrom(brandImage()))).toBeUndefined();
  });
});
