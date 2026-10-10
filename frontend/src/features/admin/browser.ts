/**
 * What the Codes screen asks of the browser itself: to save a file the page has made,
 * and to print the page (DESIGN.md §13: the CSV and the QR sheet are generated in the
 * browser). Both go through here, and a test can watch them.
 */
export const browser = {
  /**
   * Offers `text` as a file to save. The file is made in the page and goes nowhere but
   * to the user's own disk.
   *
   * The browser reads the file from the page's memory, some browsers a moment after the
   * tap. It stays there until the returned function is called: whoever offered it lets
   * go of it once what it was made from is gone.
   */
  saveFile(name: string, text: string, type: string): () => void {
    const address = URL.createObjectURL(new Blob([text], { type }));
    const link = document.createElement("a");
    link.href = address;
    link.download = name;
    link.hidden = true;
    document.body.append(link);
    link.click();
    link.remove();
    return () => URL.revokeObjectURL(address);
  },

  /** Opens the browser's print dialog for the page as its print styles show it. */
  print(): void {
    window.print();
  },
};
