"use client";

import { useEffect, useState } from "react";

/**
 * Current time on the client, refreshed on an interval so relative times do not go stale between
 * API refetches. It is null during prerender and the first paint: Next forbids reading the time
 * there, and a server-computed value would be wrong in the browser anyway.
 */
export function useNow(intervalMs = 30_000): Date | null {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    const tick = () => setNow(new Date());
    const first = setTimeout(tick, 0);
    const id = setInterval(tick, intervalMs);
    return () => {
      clearTimeout(first);
      clearInterval(id);
    };
  }, [intervalMs]);
  return now;
}
