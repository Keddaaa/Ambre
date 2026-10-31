"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

// v0.1 ships only the pages backed by real data; the others are listed in docs/ROADMAP.md.
const LINKS = [
  { href: "/", label: "Overview" },
  { href: "/markets", label: "Marchés" },
  { href: "/journal", label: "Journal" },
  { href: "/settings", label: "Réglages" },
] as const;

export function Nav() {
  const pathname = usePathname();
  return (
    <header className="border-b">
      <nav className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
        <span className="font-mono text-sm font-semibold tracking-widest">AMBRE</span>
        <span className="rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
          paper · Mana virtuel
        </span>
        <ul className="flex flex-wrap gap-4 text-sm">
          {LINKS.map(({ href, label }) => {
            const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
            return (
              <li key={href}>
                <Link
                  href={href}
                  className={cn(
                    "text-muted-foreground hover:text-foreground",
                    active && "text-foreground underline underline-offset-4",
                  )}
                >
                  {label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
    </header>
  );
}
