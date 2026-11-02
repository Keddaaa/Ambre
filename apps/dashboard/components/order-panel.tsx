"use client";

import { useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { usePlaceOrder } from "@/lib/api";
import { formatMana, formatProb } from "@/lib/format";
import { parseOrderForm } from "@/lib/order";
import type { Decision } from "@/lib/types";
import { cn } from "@/lib/utils";

function Result({ d }: { d: Decision }) {
  const placed = d.execution_status === "SIMULATED";
  return (
    <div
      role="status"
      className={cn(
        "rounded-lg border p-3 text-sm",
        placed ? "border-emerald-500/40 bg-emerald-500/5" : "border-amber-500/40 bg-amber-500/5",
      )}
    >
      <div className="font-medium">
        {placed
          ? `Mise papier placée : ${d.action} ${formatMana(d.stake)}`
          : `Non placée (${d.execution_status})`}
      </div>
      <ul className="mt-1 list-disc pl-4 text-muted-foreground">
        {d.risk_reasons.map((r) => (
          <li key={r}>{r}</li>
        ))}
      </ul>
    </div>
  );
}

/** Manual PAPER order: the owner states a probability and a stake; the risk engine decides. */
export function OrderPanel({
  marketId,
  marketProbability,
  disabledReason,
}: {
  marketId: string;
  marketProbability: number | null;
  disabledReason?: string;
}) {
  const place = usePlaceOrder();
  const [side, setSide] = useState<"YES" | "NO">("YES");
  const [probabilityPct, setProbabilityPct] = useState("");
  const [stake, setStake] = useState("10");
  const [note, setNote] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  // Kept until an answer arrives, so retrying after a network error cannot double-book.
  const requestId = useRef<string | null>(null);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    requestId.current ??= crypto.randomUUID();
    const parsed = parseOrderForm({ marketId, side, probabilityPct, stake, note }, requestId.current);
    if (!parsed.ok) {
      setFormError(parsed.error);
      return;
    }
    setFormError(null);
    place.mutate(parsed.order, {
      onSuccess: () => {
        requestId.current = null;
      },
    });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Placer une mise papier</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <p className="text-sm text-muted-foreground">
          Mana virtuel. Vous donnez votre probabilité de YES ; le risk engine plafonne la mise ou la
          refuse si l&apos;avantage est insuffisant. L&apos;action est journalisée sous USER.
        </p>
        {disabledReason ? (
          <p className="text-sm text-muted-foreground">{disabledReason}</p>
        ) : (
          <form onSubmit={submit} className="flex flex-col gap-3" noValidate>
            <div className="flex gap-1" role="group" aria-label="Côté">
              {(["YES", "NO"] as const).map((s) => (
                <Button
                  key={s}
                  type="button"
                  size="sm"
                  variant={side === s ? "default" : "outline"}
                  onClick={() => setSide(s)}
                >
                  {s}
                </Button>
              ))}
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                Ma probabilité de YES (%) — marché {formatProb(marketProbability)}
                <Input
                  inputMode="decimal"
                  value={probabilityPct}
                  onChange={(e) => setProbabilityPct(e.target.value)}
                  placeholder="ex. 62"
                />
              </label>
              <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                Mise (Mana)
                <Input
                  inputMode="decimal"
                  value={stake}
                  onChange={(e) => setStake(e.target.value)}
                />
              </label>
            </div>
            <label className="flex flex-col gap-1 text-xs text-muted-foreground">
              Note (facultatif)
              <Input value={note} maxLength={300} onChange={(e) => setNote(e.target.value)} />
            </label>
            <div>
              <Button type="submit" disabled={place.isPending}>
                {place.isPending ? "Envoi…" : "Placer la mise"}
              </Button>
            </div>
          </form>
        )}
        {formError ? (
          <p role="alert" className="text-sm text-destructive">
            {formError}
          </p>
        ) : null}
        {place.isError ? (
          <p role="alert" className="text-sm text-destructive">
            {place.error.message}
          </p>
        ) : null}
        {place.data ? <Result d={place.data} /> : null}
      </CardContent>
    </Card>
  );
}
