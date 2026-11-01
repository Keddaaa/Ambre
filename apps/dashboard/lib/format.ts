// Pure display formatters. A missing value (null) is always rendered as "—", never as 0.

export const DASH = "—";
const MINUS = "−"; // U+2212, typographic minus

function groupThousands(digits: string): string {
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, " ");
}

/** Fixed-decimal number with a typographic minus and space-grouped thousands. */
function fixed(x: number, digits: number, group: boolean): string {
  const rounded = x.toFixed(digits);
  const negative = rounded.startsWith("-") && Number(rounded) !== 0;
  const abs = rounded.replace(/^-/, "");
  const [intPart, decPart] = abs.split(".");
  const body = (group ? groupThousands(intPart) : intPart) + (decPart ? `.${decPart}` : "");
  return negative ? `${MINUS}${body}` : body;
}

export function formatNumber(x: number | null, digits = 2): string {
  return x === null ? DASH : fixed(x, digits, true);
}

export function formatSignedNumber(x: number | null, digits = 2): string {
  if (x === null) return DASH;
  const body = fixed(Math.abs(x), digits, true);
  if (Number(Math.abs(x).toFixed(digits)) === 0) return body;
  return `${x > 0 ? "+" : MINUS}${body}`;
}

export function formatProb(x: number | null): string {
  return x === null ? DASH : `${fixed(x * 100, 1, false)} %`;
}
/** Estimate with its uncertainty; a missing estimate is one dash, a missing uncertainty is left out. */
export function formatEstimate(p: number | null, u: number | null): string {
  if (p === null) return DASH;
  return u === null ? formatProb(p) : `${formatProb(p)} ± ${formatProb(u)}`;
}


export function formatMana(x: number | null): string {
  return x === null ? DASH : `M ${fixed(x, 2, true)}`;
}

/** Signed Mana amount (performance): "+M 12.30", "−M 1 200.00", "M 0.00". */
export function formatSignedMana(x: number | null): string {
  if (x === null) return DASH;
  const body = `M ${fixed(Math.abs(x), 2, true)}`;
  if (Number(Math.abs(x).toFixed(2)) === 0) return body;
  return `${x > 0 ? "+" : MINUS}${body}`;
}

/** Drawdown fraction (0.123) rendered as a loss: "−12.3 %". */
export function formatDrawdown(x: number | null): string {
  if (x === null) return DASH;
  const body = `${fixed(Math.abs(x) * 100, 1, false)} %`;
  return Number((Math.abs(x) * 100).toFixed(1)) === 0 ? body : `${MINUS}${body}`;
}

/** The API serialises naive UTC datetimes without an offset: treat them as UTC. */
export function parseApiDate(iso: string | null): Date | null {
  if (!iso) return null;
  const hasOffset = /(Z|[+-]\d{2}:?\d{2})$/.test(iso);
  const d = new Date(hasOffset ? iso : `${iso}Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function formatRelativeTime(iso: string | null, now: Date): string {
  const d = parseApiDate(iso);
  if (d === null) return DASH;
  const seconds = Math.round((now.getTime() - d.getTime()) / 1000);
  const abs = Math.abs(seconds);
  if (abs < 60) return "à l'instant";
  let amount: string;
  if (abs < 3600) amount = `${Math.floor(abs / 60)} min`;
  else if (abs < 86400) amount = `${Math.floor(abs / 3600)} h`;
  else amount = `${Math.floor(abs / 86400)} j`;
  return seconds > 0 ? `il y a ${amount}` : `dans ${amount}`;
}

/** Absolute UTC timestamp: "2026-10-31 09:05 UTC". */
export function formatDateTime(iso: string | null): string {
  const d = parseApiDate(iso);
  if (d === null) return DASH;
  return `${d.toISOString().slice(0, 16).replace("T", " ")} UTC`;
}
