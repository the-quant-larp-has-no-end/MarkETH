"use client";

import { useState } from "react";

import { Screen } from "@/components/shell";

import { MyProposals } from "./my-proposals";
import { ProposeForm } from "./propose-form";

/**
 * Propose market (`/propose`, DESIGN.md §13): an inner screen with the form, and below it
 * the viewer's own proposals. A proposal that was sent empties the form; the list is read
 * again and shows it on top.
 */
export function ProposeScreen() {
  // Each proposal that was sent starts a fresh form.
  const [sent, setSent] = useState(0);

  return (
    <Screen top="inner" title="Propose a market" width="narrow">
      <ProposeForm key={sent} onSent={() => setSent((count) => count + 1)} />
      <MyProposals />
    </Screen>
  );
}
