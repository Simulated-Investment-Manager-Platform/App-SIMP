import type { Metadata } from "next";
import * as React from "react";

import { Shell } from "@/components/Shell";

import "./globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "SIMP",
  description: "Simulated Investment Manager Platform: funds, order chain, execution on market data, TCA.",
};

// Dark is the default theme; a stored preference is applied before paint to avoid a flash.
const themeScript = `try{var t=localStorage.getItem('simp-theme');document.documentElement.dataset.theme=t==='light'?'light':'dark'}catch(e){document.documentElement.dataset.theme='dark'}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-theme="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="min-h-screen">
        <Providers>
          <React.Suspense>
            <Shell>{children}</Shell>
          </React.Suspense>
        </Providers>
      </body>
    </html>
  );
}
