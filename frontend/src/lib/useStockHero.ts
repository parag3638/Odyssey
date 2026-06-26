"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  getStock,
  getStockHistory,
  type HistoryPoint,
  type StockDetailData,
} from "@/lib/api";
import { makeSeries } from "@/lib/sample";

/** Loads a single stock's fundamentals (for the KPI strip + P/E tile) and price
 *  history (for the hero chart) for the dashboard's stock viewer. Caches per
 *  symbol and per symbol+range, so re-selecting a previously viewed stock or
 *  range is instant with no refetch or skeleton flash. Mirrors the fetch/series
 *  logic of the /stocks/[symbol] detail page. */
export function useStockHero(
  symbol: string,
  range: string,
  fallbackPrice: number | null,
  up: boolean,
) {
  const [detail, setDetail] = useState<StockDetailData | null>(null);
  const [history, setHistory] = useState<HistoryPoint[]>([]);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [loadingChart, setLoadingChart] = useState(false);

  const detailCache = useRef(new Map<string, StockDetailData>());
  const histCache = useRef(new Map<string, HistoryPoint[]>());

  // Fundamentals — keyed by symbol.
  useEffect(() => {
    if (!symbol) {
      setDetail(null);
      return;
    }
    const cached = detailCache.current.get(symbol);
    if (cached) {
      setDetail(cached);
      setLoadingDetail(false);
      return;
    }
    let cancelled = false;
    setDetail(null);
    setLoadingDetail(true);
    getStock(symbol)
      .then((d) => {
        if (cancelled) return;
        detailCache.current.set(symbol, d);
        setDetail(d);
      })
      .catch(() => {
        if (!cancelled) setDetail(null);
      })
      .finally(() => {
        if (!cancelled) setLoadingDetail(false);
      });
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  // Price history — keyed by symbol + range.
  useEffect(() => {
    if (!symbol) {
      setHistory([]);
      return;
    }
    const key = `${symbol}|${range}`;
    const cached = histCache.current.get(key);
    if (cached) {
      setHistory(cached);
      setLoadingChart(false);
      return;
    }
    let cancelled = false;
    setLoadingChart(true);
    getStockHistory(symbol, range)
      .then((h) => {
        if (cancelled) return;
        histCache.current.set(key, h);
        setHistory(h);
      })
      .catch(() => {
        if (!cancelled) setHistory([]);
      })
      .finally(() => {
        if (!cancelled) setLoadingChart(false);
      });
    return () => {
      cancelled = true;
    };
  }, [symbol, range]);

  const realChart = history.length > 1;
  const series = useMemo(
    () =>
      realChart
        ? history.map((h) => h.price)
        : makeSeries(120, fallbackPrice ?? 100, up ? 8 : -8, symbol.length + 3),
    [realChart, history, fallbackPrice, up, symbol],
  );
  const dates = useMemo(
    () =>
      realChart
        ? history.map((h) =>
            new Date(h.t).toLocaleString("en-US", { month: "short", day: "numeric" }),
          )
        : undefined,
    [realChart, history],
  );

  return { detail, series, dates, realChart, loadingChart, loadingDetail };
}
