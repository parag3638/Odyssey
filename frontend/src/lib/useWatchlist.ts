"use client";

import { useCallback, useEffect, useState } from "react";
import {
  addWatchlist,
  getWatchlist,
  removeWatchlist,
  type StockRow,
} from "@/lib/api";

/** Manages the followed-symbols set + rows; powers star toggles everywhere. */
export function useWatchlist() {
  const [rows, setRows] = useState<StockRow[]>([]);
  const [symbols, setSymbols] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const w = await getWatchlist();
      setRows(w);
      setSymbols(new Set(w.map((r) => r.symbol)));
    } catch {
      /* backend down — keep empty */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const w = await getWatchlist();
        if (!cancelled) {
          setRows(w);
          setSymbols(new Set(w.map((r) => r.symbol)));
        }
      } catch {
        /* backend down — keep empty */
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const toggle = useCallback(
    async (symbol: string) => {
      const s = symbol.toUpperCase();
      const has = symbols.has(s);
      // optimistic
      setSymbols((prev) => {
        const n = new Set(prev);
        if (has) n.delete(s);
        else n.add(s);
        return n;
      });
      try {
        const updated = has ? await removeWatchlist(s) : await addWatchlist(s);
        setRows(updated);
        setSymbols(new Set(updated.map((r) => r.symbol)));
      } catch {
        void refresh();
      }
    },
    [symbols, refresh],
  );

  return {
    rows,
    symbols,
    loading,
    toggle,
    refresh,
    isStarred: (s: string) => symbols.has(s.toUpperCase()),
  };
}
