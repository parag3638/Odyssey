"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  getAccountSummary,
  getPositions,
  getQuotes,
  listAccounts,
  type AccountOut,
  type Position,
  type QuoteOut,
} from "@/lib/api";
import type { HoldingView } from "@/lib/types";

/** Loads the first account's positions + live quotes + cash and derives the
 *  view-model (holdings, totals, today's return) shared by Overview & Positions. */
export function usePortfolio() {
  const [account, setAccount] = useState<AccountOut | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [quotes, setQuotes] = useState<QuoteOut[]>([]);
  const [cash, setCash] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadAll = useCallback(async (accountId: string) => {
    setLoading(true);
    setError(null);
    try {
      // Fetch all three together and commit in one render so the balance never
      // shows an intermediate value (e.g. holdings-at-cost before cash loads).
      const [pos, q, summary] = await Promise.all([
        getPositions(accountId),
        getQuotes(accountId).catch(() => [] as QuoteOut[]),
        getAccountSummary(accountId).catch(() => ({ cash: null })),
      ]);
      setPositions(pos);
      setQuotes(q);
      setCash(summary.cash);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load positions.");
      setPositions([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const accounts = await listAccounts();
        if (cancelled) return;
        const first = accounts[0] ?? null;
        setAccount(first);
        if (first) await loadAll(first.id);
        else {
          setLoading(false);
          setError("No accounts found. Create one in the backend to begin.");
        }
      } catch (e) {
        if (cancelled) return;
        setLoading(false);
        setError(e instanceof Error ? e.message : "Could not reach the backend.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loadAll]);

  const refresh = useCallback(() => {
    if (account) void loadAll(account.id);
  }, [account, loadAll]);

  const holdings: HoldingView[] = useMemo(() => {
    const qmap = new Map(quotes.map((q) => [q.symbol, q]));
    return positions.map((p) => {
      const q = qmap.get(p.symbol);
      return {
        symbol: p.symbol,
        qty: p.qty,
        avgCost: p.avg_entry_price,
        price: q?.price ?? null,
        prevClose: q?.prev_close ?? null,
      };
    });
  }, [positions, quotes]);

  const totalHoldings = useMemo(
    () => holdings.reduce((s, h) => s + h.qty * (h.price ?? h.avgCost), 0),
    [holdings],
  );
  const equity = totalHoldings + (cash ?? 0);
  const balance = cash != null ? equity : totalHoldings;

  const { todayAmount, todayPct } = useMemo(() => {
    let prev = cash ?? 0;
    let cur = cash ?? 0;
    for (const h of holdings) {
      const c = h.price ?? h.avgCost;
      const pc = h.prevClose ?? c;
      prev += h.qty * pc;
      cur += h.qty * c;
    }
    const amt = cur - prev;
    return { todayAmount: amt, todayPct: prev > 0 ? (amt / prev) * 100 : 0 };
  }, [holdings, cash]);

  const allTime = useMemo(() => {
    let cost = 0;
    let value = 0;
    for (const h of holdings) {
      cost += h.qty * h.avgCost;
      value += h.qty * (h.price ?? h.avgCost);
    }
    const amt = value - cost;
    return { amount: amt, pct: cost > 0 ? (amt / cost) * 100 : 0 };
  }, [holdings]);

  return {
    account,
    positions,
    holdings,
    cash,
    totalHoldings,
    equity,
    balance,
    todayAmount,
    todayPct,
    allTime,
    loading,
    error,
    refresh,
    hasData: positions.length > 0,
  };
}
