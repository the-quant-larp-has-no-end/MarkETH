/**
 * One copy of every brand image that has loaded, kept until the page goes (DESIGN.md §8).
 *
 * A browser holds a loaded image for as long as an element of the page shows it. A brand
 * image that no screen shows for a while (the logo, on a screen with a back arrow) is
 * dropped, and the next element that shows it asks the server again and is painted empty
 * first. A copy is an `img` of this document that is never attached to it: while it
 * lives, every element with its address takes the picture from it at once and asks
 * nobody.
 *
 * Nothing is kept in browser storage, and a copy goes when its page goes.
 */
const copies = new Map<string, HTMLImageElement>();

/** Keeps the image at `address`, which an element of the page has just loaded. */
export function keepForSession(address: string): void {
  if (address === "" || copies.has(address)) return;
  const copy = document.createElement("img");
  copy.setAttribute("decoding", "sync");
  copy.src = address;
  copies.set(address, copy);
}

/** The copy that is kept of the image at `address`, once that image has loaded. */
export function sessionCopy(address: string): HTMLImageElement | undefined {
  return copies.get(address);
}
