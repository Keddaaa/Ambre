import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { Suspense } from "react";

import { ActivityPanel } from "@/components/activity";
import { MobileNav, Sidebar } from "@/components/nav";

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
      <body className="min-h-full md:flex">
        <Providers>
          <Suspense fallback={<div className="hidden w-[17.5rem] border-r md:block" />}>
            <Sidebar />
          </Suspense>
          <div className="flex min-w-0 flex-1 flex-col">
            <Suspense fallback={<div className="h-12 border-b md:hidden" />}>
              <MobileNav />
            </Suspense>
            <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-6 px-4 py-6 md:px-8">
              {children}
            </main>
          </div>
          <Suspense fallback={<div className="hidden w-72 border-l xl:block" />}>
            <ActivityPanel />
          </Suspense>
        </Providers>
      </body>
    </html>
  );
}
