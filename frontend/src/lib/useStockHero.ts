"use client";

import { useMemo } from "react";
import useSWR from "swr";
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
  const detailQuery = useSWR<StockDetailData>(
    symbol ? ["stock-detail", symbol] : null,
    () => getStock(symbol),
    { dedupingInterval: 5 * 60_000, revalidateOnFocus: false, keepPreviousData: true },
  );
  const historyQuery = useSWR<HistoryPoint[]>(
    symbol ? ["stock-history", symbol, range] : null,
    () => getStockHistory(symbol, range),
    { dedupingInterval: 5 * 60_000, revalidateOnFocus: false, keepPreviousData: true },
  );

  const detail = detailQuery.data ?? null;
  const history = useMemo(() => historyQuery.data ?? [], [historyQuery.data]);
  const loadingDetail = detailQuery.isLoading;
  const loadingChart = historyQuery.isLoading;

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
