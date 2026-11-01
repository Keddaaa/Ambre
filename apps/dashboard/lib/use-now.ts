"use client";

import { useEffect, useState } from "react";

/** Current time, refreshed on an interval so relative times do not go stale between API refetches. */
export function useNow(intervalMs = 30_000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}
