import type { OrderRequest } from "./types";

export interface OrderForm {
  marketId: string;
  side: "YES" | "NO";
  /** The person's own P(YES), in percent, as typed. */
  probabilityPct: string;
  stake: string;
  note: string;
}

export type ParsedOrder = { ok: true; order: OrderRequest } | { ok: false; error: string };

function toNumber(raw: string): number {
  const t = raw.trim().replace(",", ".");
  return t === "" ? Number.NaN : Number(t);
}

/** Validate what the person typed; the API and the risk engine still have the last word. */
export function parseOrderForm(form: OrderForm, requestId: string): ParsedOrder {
  const pct = toNumber(form.probabilityPct);
  if (!Number.isFinite(pct) || pct <= 0 || pct >= 100) {
    return { ok: false, error: "Probabilité : un pourcentage strictement entre 0 et 100." };
  }
  const stake = toNumber(form.stake);
  if (!Number.isFinite(stake) || stake <= 0) {
    return { ok: false, error: "Mise : un montant en Mana strictement positif." };
  }
  const note = form.note.trim();
  return {
    ok: true,
    order: {
      market_id: form.marketId,
      side: form.side,
      probability: pct / 100,
      stake,
      actor: "USER",
      request_id: requestId,
      note: note === "" ? null : note,
    },
  };
}
