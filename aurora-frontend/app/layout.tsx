import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import { THEME_BOOT_SCRIPT } from "@/lib/preferences";

import "@fontsource-variable/instrument-sans/wght.css";
import "@fontsource-variable/spline-sans-mono/wght.css";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Aurora", template: "%s · Aurora" },
  description: "Demand forecasting and inventory intelligence",
};

export const viewport: Viewport = { themeColor: "#ffffff", width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    // The boot script sets data-theme before React hydrates, so the attribute differs from the server render.
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT_SCRIPT }} />
      </head>
      <body className="antialiased">{children}</body>
    </html>
  );
}
