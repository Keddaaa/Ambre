import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";

import { Nav } from "@/components/nav";

import "./globals.css";
import { Providers } from "./providers";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "AMBRE — paper trading",
  description: "AMBRE 0.1 : moteur de recherche en paper trading sur Manifold (Mana virtuel).",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="fr"
      className={`dark ${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="flex min-h-full flex-col">
        <Providers>
          <Nav />
          <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-6 px-4 py-6">
            {children}
          </main>
        </Providers>
      </body>
    </html>
  );
}
