"use client";

import Link from "next/link";
import { useState } from "react";

import { Pager, useClampOffset } from "@/components/pager";
import { QueryState } from "@/components/state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useMarkets } from "@/lib/api";
import { formatDateTime, formatNumber, formatProb } from "@/lib/format";
import type { MarketStatus } from "@/lib/types";

const LIMIT = 50;
const STATUSES: { value: MarketStatus; label: string }[] = [
  { value: "open", label: "Ouverts" },
  { value: "resolved", label: "Résolus" },
  { value: "all", label: "Tous" },
];

export default function MarketsPage() {
  const [status, setStatus] = useState<MarketStatus>("open");
  const [draft, setDraft] = useState("");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);

  const markets = useMarkets({ status, q: q || undefined, limit: LIMIT, offset });
  useClampOffset(markets.data?.total, offset, LIMIT, setOffset);
  // keepPreviousData keeps the old rows while a new filter loads: make that visible.
  const stale = markets.isPlaceholderData;

  return (
    <>
      <div>
        <h1 className="text-lg font-semibold">Marchés</h1>
        <p className="text-sm text-muted-foreground">
          Marchés binaires synchronisés depuis Manifold. L&apos;estimation d&apos;AMBRE est une
          baseline non validée : l&apos;EV est théorique.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <div className="flex gap-1" role="group" aria-label="Statut">
          {STATUSES.map((s) => (
            <Button
              key={s.value}
              size="sm"
              variant={status === s.value ? "default" : "outline"}
              onClick={() => {
                setStatus(s.value);
                setOffset(0);
              }}
            >
              {s.label}
            </Button>
          ))}
        </div>
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            setQ(draft.trim());
            setOffset(0);
          }}
        >
          <Input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Rechercher une question…"
            aria-label="Recherche"
            className="w-64"
          />
          <Button type="submit" size="sm" variant="outline">
            Chercher
          </Button>
        </form>
      </div>

      <QueryState query={markets} empty="Aucun marché ne correspond. Lancez `ambre sync` si la base est vide.">
        {(page) => (
          <div
            className={`flex flex-col gap-3 transition-opacity ${stale ? "pointer-events-none opacity-50" : ""}`}
            aria-busy={stale}
          >
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Question</TableHead>
                  <TableHead className="text-right">Marché</TableHead>
                  <TableHead className="text-right">AMBRE ± incertitude</TableHead>
                  <TableHead className="text-right">EV / mana</TableHead>
                  <TableHead className="text-right">Liquidité</TableHead>
                  <TableHead className="text-right">Parieurs</TableHead>
                  <TableHead>Clôture</TableHead>
                  <TableHead>Dernière action</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {page.items.map((m) => (
                  <TableRow key={m.id}>
                    <TableCell className="max-w-sm truncate">
                      <Link className="hover:underline" href={`/markets/${encodeURIComponent(m.id)}`}>
                        {m.question}
                      </Link>
                      {m.is_resolved ? (
                        <Badge variant="secondary" className="ml-2">
                          {m.resolution ?? "résolu"}
                        </Badge>
                      ) : null}
                    </TableCell>
                    <TableCell className="text-right font-mono tabular-nums">{formatProb(m.probability)}</TableCell>
                    <TableCell className="text-right font-mono tabular-nums">
                      {m.ambre_estimate === null
                        ? "—"
                        : `${formatProb(m.ambre_estimate)} ± ${formatProb(m.ambre_uncertainty)}`}
                    </TableCell>
                    <TableCell className="text-right font-mono tabular-nums">
                      {formatNumber(m.ambre_ev_per_mana, 3)}
                    </TableCell>
                    <TableCell className="text-right font-mono tabular-nums">
                      {formatNumber(m.total_liquidity, 0)}
                    </TableCell>
                    <TableCell className="text-right font-mono tabular-nums">
                      {formatNumber(m.unique_bettor_count, 0)}
                    </TableCell>
                    <TableCell>{formatDateTime(m.close_at)}</TableCell>
                    <TableCell>{m.last_action ? <Badge variant="outline">{m.last_action}</Badge> : "—"}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <Pager offset={offset} limit={LIMIT} total={page.total} onChange={setOffset} />
          </div>
        )}
      </QueryState>
    </>
  );
}
