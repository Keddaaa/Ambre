// Pure display helpers of the statistics page. A missing value is a dash, never 0.

import { DASH, formatNumber, formatProb } from "./format";

/** Warning above the win rate, or null when enough bets have a result. */
export function betSampleNotice(decided: number, minSample: number): string | null {
  if (decided === 0) return "Aucun pari gagné ou perdu pour l'instant : pas de taux de réussite.";
  if (decided < minSample) {
    const bets = decided > 1 ? `${decided} paris gagnés ou perdus` : `${decided} pari gagné ou perdu`;
    return `Échantillon insuffisant : ${bets} sur ${minSample} minimum. Ne tirez aucune conclusion.`;
  }
  return null;
}

export function formatRecord(won: number, lost: number): string {
  return `${won} G – ${lost} P`;
}

/** Positive run of wins, negative run of losses. */
export function formatStreak(streak: number): string {
  if (streak === 0) return DASH;
  const n = Math.abs(streak);
  return `${n} ${streak > 0 ? "gagné" : "perdu"}${n > 1 ? "s" : ""}`;
}

export function formatProfitFactor(x: number | null): string {
  return formatNumber(x, 2);
}

export function resultLabel(pnl: number, resolution: string | null): string {
  if (resolution === "CANCEL") return "Remboursé";
  if (pnl === 0) return "Neutre";
  return pnl > 0 ? "Gagné" : "Perdu";
}

export function formatRateInterval(ci: readonly [number, number] | null): string {
  return ci === null ? DASH : `[${formatProb(ci[0])} ; ${formatProb(ci[1])}]`;
}
