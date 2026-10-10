import type { Metadata, Viewport } from "next";
import { Barlow, Inter } from "next/font/google";
import type { ReactNode } from "react";

import "./globals.css";
import { Providers } from "./providers";

// next/font downloads both families at build time and serves them from the app's own
// origin (DESIGN.md §5); the Content-Security-Policy allows no font CDN.
const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });
const barlow = Barlow({
  subsets: ["latin"],
  weight: ["500", "600", "700"],
  variable: "--font-barlow",
  display: "swap",
});

export const metadata: Metadata = {
  title: { default: "MarkETH", template: "%s · MarkETH" },
  description: "A prediction market for ETH Zurich students.",
  applicationName: "MarkETH",
  icons: {
    icon: "/brand/favicon.png",
    apple: "/brand/apple-touch-icon.png",
  },
  // The app is reachable only behind the portal's sign-in.
  robots: { index: false, follow: false },
};

// A meta tag cannot read a CSS variable: this is the value of --background (DESIGN.md §6).
export const viewport: Viewport = {
  themeColor: "#1e2749",
  colorScheme: "dark",
  viewportFit: "cover",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${barlow.variable}`}>
      <head>
        {/* Linked by hand: a browser fetches a manifest without cookies unless told
            otherwise, and the app is only reachable with them. */}
        <link rel="manifest" href="/manifest.webmanifest" crossOrigin="use-credentials" />
      </head>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
