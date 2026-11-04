// Ambre's own account of a decision, built only from what was journaled at decision time.
// She speaks in the first person, in the feminine. A reason she cannot translate is kept as is.

import { formatEstimate, formatMana, formatNumber, formatProb } from "./format";
import type { Decision, ResearchSource } from "./types";

export interface Justification {
  headline: string;
  lines: string[];
}

export interface CitedSource {
  label: string;
  /** null when there is no link, or none safe to open. */
  url: string | null;
  detail?: string;
}

export interface DecisionSources {
  market: CitedSource;
  researchNote: string;
  research: CitedSource[];
}

/** Links come from outside feeds: only http(s) may reach an href. */
export function safeHttpUrl(url: string | null): string | null {
  if (url === null) return null;
  try {
    const { protocol } = new URL(url);
    return protocol === "http:" || protocol === "https:" ? url : null;
  } catch {
    return null;
  }
}

function citeResearch(s: ResearchSource): CitedSource {
  const trust = s.evidence_level === "unverified" ? "non vérifié" : s.evidence_level;
  return {
    label: s.title ?? s.provider_id,
    url: safeHttpUrl(s.url),
    detail: `${s.provider_id} · ${trust}`,
  };
}

export function decisionSources(d: Decision): DecisionSources {
  return {
    market: { label: "Page du marché sur Manifold", url: safeHttpUrl(d.market_url) },
    researchNote:
      d.research_sources.length === 0
        ? "Aucune recherche n'était liée à ce marché quand j'ai décidé."
        : "Lu avant de décider, sans effet sur mon estimation :",
    research: d.research_sources.map(citeResearch),
  };
}

const INVALIDATIONS: Record<string, string> = {
  "market moves past p_cons": "le prix dépassait mon estimation prudente",
  "estimate uncertainty exceeds edge": "mon incertitude dépassait mon avantage",
};

function riskLine(reason: string): string | null {
  let m = /^edge after impact (\S+) >= /.exec(reason);
  if (m) return null; // the passing check is already told by the edge line
  m = /^edge after impact (\S+) < (\S+)$/.exec(reason);
  if (m) {
    return `Une fois ma mise répercutée sur le prix, il ne me resterait que ${formatNumber(Number(m[1]))} mana par mana misé, sous mon seuil de ${m[2]}.`;
  }
  if (/already has an open position$/.test(reason)) {
    return "J'ai déjà une position ouverte sur ce marché.";
  }
  m = /^stake capped from (\S+) to (\S+)$/.exec(reason);
  if (m) {
    return `Ma mise a été réduite de ${formatMana(Number(m[1]))} à ${formatMana(Number(m[2]))} par mes limites de risque.`;
  }
  m = /^stake (\S+) below min stake (\S+)$/.exec(reason);
  if (m) {
    return `Ma mise de ${formatMana(Number(m[1]))} serait sous le minimum de ${formatMana(Number(m[2]))}.`;
  }
  m = /^survival state (\S+) forbids new positions$/.exec(reason);
  if (m) return `Mon état de survie (${m[1]}) m'interdit d'ouvrir une position.`;
  if (reason === "exposure unknown") {
    return "Je ne connais pas mon exposition exacte, alors je ne prends pas ce risque.";
  }
  return reason;
}

function riskLines(d: Decision): string[] {
  return d.risk_reasons.map(riskLine).filter((line): line is string => line !== null);
}

function resultLine(d: Decision): string[] {
  if (d.execution_status !== "SIMULATED" || d.outcome === null || d.payout === null) return [];
  if (d.outcome === "CANCEL") return ["Résultat : marché annulé, ma mise m'a été remboursée."];
  const pnl = d.payout - d.stake;
  const money =
    pnl === 0 ? "je récupère ma mise" : `j'ai ${pnl > 0 ? "gagné" : "perdu"} ${formatMana(Math.abs(pnl))}`;
  return [`Résultat : marché résolu ${d.outcome}, ${money}.`];
}

function viewLine(d: Decision): string {
  const view = `Le marché donne YES à ${formatProb(d.market_probability)}, moi à ${formatEstimate(d.estimated_probability, d.uncertainty)}`;
  return d.action === "NO_TRADE"
    ? view
    : `${view} : je le trouve trop ${d.action === "YES" ? "bas" : "haut"}.`;
}

function edgeLine(d: Decision): string[] {
  if (d.conservative_probability === null) return [];
  const prudent = `Même prudente, je retiens ${formatProb(d.conservative_probability)}`;
  return [
    d.ev_per_mana === null
      ? `${prudent}.`
      : `${prudent}, soit un avantage attendu de ${formatNumber(d.ev_per_mana)} mana par mana misé.`,
  ];
}

function sourceLine(d: Decision): string[] {
  const onlyManifold = d.source_ids.length > 0 && d.source_ids.every((s) => s.startsWith("manifold:"));
  return onlyManifold
    ? ["Mon estimation repose uniquement sur les données Manifold, sans aucune actualité."]
    : [];
}

function invalidationLine(d: Decision): string[] {
  if (d.invalidation.length === 0) return [];
  const parts = d.invalidation.map((i) => INVALIDATIONS[i] ?? i);
  return [`Je changerais d'avis si ${parts.join(" ou si ")}.`];
}

export function explainDecision(d: Decision): Justification {
  if (d.actor !== "AMBRE") {
    return {
      headline:
        d.execution_status === "SIMULATED"
          ? `Ordre manuel (${d.actor}) : ${d.action} pour ${formatMana(d.stake)}.`
          : `Ordre manuel (${d.actor}) non exécuté.`,
      lines: [...riskLines(d), ...resultLine(d)],
    };
  }
  if (d.action === "NO_TRADE") {
    const noEdge = d.reasons.some((r) => r.startsWith("no edge"));
    return {
      headline: "Je me suis abstenue.",
      lines: [
        noEdge
          ? `${viewLine(d)} : une fois mon incertitude déduite, je ne vois plus d'avantage.`
          : `${viewLine(d)}.`,
        ...sourceLine(d),
      ],
    };
  }
  if (d.execution_status !== "SIMULATED") {
    return { headline: "J'ai renoncé à ce pari.", lines: [viewLine(d), ...riskLines(d)] };
  }
  return {
    headline: `J'ai parié ${d.action} pour ${formatMana(d.stake)}.`,
    lines: [
      viewLine(d),
      ...edgeLine(d),
      ...riskLines(d),
      ...sourceLine(d),
      ...invalidationLine(d),
      ...resultLine(d),
    ],
  };
}
