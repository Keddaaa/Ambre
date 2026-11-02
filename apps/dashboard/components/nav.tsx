"use client";

import { ChartColumn, LayoutDashboard, Settings, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { useSystem } from "@/lib/api";
import { formatRelativeTime } from "@/lib/format";
import { NAV_SECTIONS, activeSection, isActive } from "@/lib/nav";
import { useNow } from "@/lib/use-now";
import { cn } from "@/lib/utils";

const SECTION_ICONS: Record<string, LucideIcon> = {
  pilotage: LayoutDashboard,
  analyse: ChartColumn,
  systeme: Settings,
};

function StatusFooter() {
  const { data } = useSystem();
  const now = useNow();
  const halted = data?.halted === true;
  return (
    <div className="rounded-lg border bg-card/50 p-3 text-xs">
      <div className="flex items-center gap-2">
        <span
          aria-hidden
          className={cn(
            "size-2 rounded-full",
            data === undefined ? "bg-muted-foreground" : halted ? "bg-red-400" : "bg-emerald-400",
          )}
        />
        <span className="font-medium">
          {data === undefined ? "API…" : halted ? "Arrêt d'urgence" : "Actif"}
        </span>
      </div>
      <div className="mt-1.5 text-muted-foreground">
        Mode {data?.execution_mode ?? "—"} · Mana virtuel
      </div>
      <div className="text-muted-foreground">
        Sync {formatRelativeTime(data?.last_sync_at ?? null, now)}
      </div>
    </div>
  );
}

/** Desktop: icon rail (one icon per section) + panel with the section's pages. */
export function Sidebar() {
  const pathname = usePathname();
  const current = activeSection(pathname);
  return (
    <aside className="sticky top-0 hidden h-screen shrink-0 border-r bg-sidebar md:flex">
      <div className="flex w-14 flex-col items-center gap-2 border-r py-4">
        <Link
          href="/"
          aria-label="AMBRE — accueil"
          className="mb-3 flex size-9 items-center justify-center rounded-lg border bg-card font-mono text-sm font-semibold"
        >
          A
        </Link>
        {NAV_SECTIONS.map((section) => {
          const Icon = SECTION_ICONS[section.id] ?? LayoutDashboard;
          const selected = section.id === current.id;
          return (
            <Link
              key={section.id}
              href={section.links[0].href}
              aria-label={section.label}
              title={section.label}
              aria-current={selected ? "true" : undefined}
              className={cn(
                "flex size-9 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:text-foreground",
                selected && "bg-muted text-foreground",
              )}
            >
              <Icon className="size-4" />
            </Link>
          );
        })}
      </div>
      <div className="flex w-52 flex-col gap-6 p-4">
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm font-semibold tracking-widest">AMBRE</span>
          <span className="rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
            paper
          </span>
        </div>
        <nav aria-label={current.label} className="flex flex-col gap-1">
          <span className="px-2 pb-1 text-[10px] font-medium uppercase tracking-widest text-muted-foreground">
            {current.label}
          </span>
          {current.links.map(({ href, label }) => {
            const active = isActive(href, pathname);
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "rounded-lg px-2 py-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground",
                  active && "bg-muted text-foreground",
                )}
              >
                {label}
              </Link>
            );
          })}
        </nav>
        <div className="mt-auto">
          <StatusFooter />
        </div>
      </div>
    </aside>
  );
}

/** Mobile: single scrollable bar with every page. */
export function MobileNav() {
  const pathname = usePathname();
  return (
    <header className="border-b md:hidden">
      <nav className="flex items-center gap-4 overflow-x-auto px-4 py-3 text-sm">
        <span className="font-mono text-sm font-semibold tracking-widest">AMBRE</span>
        {NAV_SECTIONS.flatMap((s) => s.links).map(({ href, label }) => (
          <Link
            key={href}
            href={href}
            aria-current={isActive(href, pathname) ? "page" : undefined}
            className={cn(
              "whitespace-nowrap text-muted-foreground",
              isActive(href, pathname) && "text-foreground underline underline-offset-4",
            )}
          >
            {label}
          </Link>
        ))}
      </nav>
    </header>
  );
}
