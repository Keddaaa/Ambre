"use client";

import { useEffect } from "react";

import { Button } from "@/components/ui/button";

/**
 * If the row count shrinks (e.g. markets resolved between two refreshes) while the user is
 * on a later page, step back to the last page that still exists instead of showing an
 * empty page with no way back.
 */
export function useClampOffset(
  total: number | undefined,
  offset: number,
  limit: number,
  setOffset: (offset: number) => void,
) {
  useEffect(() => {
    if (total === undefined || offset === 0 || offset < total) return;
    setOffset(total === 0 ? 0 : Math.floor((total - 1) / limit) * limit);
  }, [total, offset, limit, setOffset]);
}

/** Offset/limit pagination controls. `total` is the full row count reported by the API. */
export function Pager({
  offset,
  limit,
  total,
  onChange,
}: {
  offset: number;
  limit: number;
  total: number;
  onChange: (offset: number) => void;
}) {
  const first = total === 0 ? 0 : offset + 1;
  const last = Math.min(offset + limit, total);
  return (
    <div className="flex items-center justify-between gap-3 text-sm text-muted-foreground">
      <span className="tabular-nums">
        {first}–{last} sur {total}
      </span>
      <div className="flex gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={offset === 0}
          onClick={() => onChange(Math.max(0, offset - limit))}
        >
          Précédent
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={offset + limit >= total}
          onClick={() => onChange(offset + limit)}
        >
          Suivant
        </Button>
      </div>
    </div>
  );
}
