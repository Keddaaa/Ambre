// Only pages backed by real data are listed here; the others live in docs/ROADMAP.md.
export interface NavLinkDef {
  href: string;
  label: string;
}

export interface NavSection {
  id: string;
  label: string;
  links: readonly NavLinkDef[];
}

export const NAV_SECTIONS: readonly NavSection[] = [
  {
    id: "pilotage",
    label: "Pilotage",
    links: [
      { href: "/", label: "Overview" },
      { href: "/markets", label: "Marchés" },
      { href: "/journal", label: "Journal" },
    ],
  },
  {
    id: "analyse",
    label: "Analyse",
    links: [
      { href: "/stats", label: "Statistiques" },
      { href: "/evaluation", label: "Évaluation" },
    ],
  },
  {
    id: "systeme",
    label: "Système",
    links: [{ href: "/settings", label: "Réglages" }],
  },
];

export function isActive(href: string, pathname: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function activeSection(pathname: string): NavSection {
  return (
    NAV_SECTIONS.find((s) => s.links.some((l) => isActive(l.href, pathname))) ?? NAV_SECTIONS[0]
  );
}
