import type { Metadata } from "next";
import { Manrope, DM_Mono } from "next/font/google";

import "./globals.css";

/**
 * Fonts are self-hosted by next/font (no render-blocking @import, automatic
 * `display: swap`, zero layout shift). globals.css / tailwind read the CSS vars.
 */
const sans = Manrope({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800"],
  variable: "--font-sans",
  display: "swap",
});

const mono = DM_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "ADF Forge",
  description: "Control plane for governed Azure Data Factory delivery",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
