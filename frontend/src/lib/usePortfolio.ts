"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  getPositionsBootstrap,
  type AccountOut,
  type PortfolioHealthOut,
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
  const [portfolioHealth, setPortfolioHealth] = useState<PortfolioHealthOut | null>(null);
  const [healthKey, setHealthKey] = useState<string | null>(null);
  const [healthPending, setHealthPending] = useState(false);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const overview = await getPositionsBootstrap();
      setAccount(overview.account);
      setPositions(overview.positions);
      setQuotes(overview.quotes);
      setCash(overview.cash);
      setPortfolioHealth(overview.portfolio_health);
      setHealthKey(overview.health_key);
      setHealthPending(overview.health_pending);
      if (!overview.account) setError("No accounts found. Connect one to begin.");
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
      if (!cancelled) await loadAll();
    })();
    return () => {
      cancelled = true;
    };
  }, [loadAll]);

  const refresh = useCallback(() => {
    void loadAll();
  }, [loadAll]);

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
    portfolioHealth,
    healthKey,
    healthPending,
    refresh,
    reloadAccount: loadAll,
    hasData: positions.length > 0,
  };
}
