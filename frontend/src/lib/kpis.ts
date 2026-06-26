/* Shared formatters that turn a stock's fundamentals into display rows.
   Used by the stock detail page and the dashboard's single-stock hero. */

import { compact, money } from "@/lib/format";
import type { KpiItem } from "@/lib/types";
import type { StockDetailData } from "@/lib/api";

/** Build the KPI strip rows (Mkt cap, P/E, EPS, Beta, Div yield, 52W hi/lo,
    gross margin) from a stock's Finnhub-style fundamentals. */
export function buildKpis(d: StockDetailData): KpiItem[] {
  const f = d.fundamentals || {};
  const n = (k: string): number | null =>
    typeof f[k] === "number" ? (f[k] as number) : null;
  const pe = n("peTTM") ?? n("peBasicExclExtraTTM");
  const eps = n("epsTTM") ?? n("epsBasicExclExtraItemsTTM");
  const dy = n("dividendYieldIndicatedAnnual") ?? n("currentDividendYieldTTM");
  const mc = d.market_cap ?? (n("marketCapitalization") ? n("marketCapitalization")! * 1e6 : null);
  return [
    { k: "Mkt cap", v: mc != null ? compact(mc) : "—" },
    { k: "P/E", v: pe != null ? pe.toFixed(1) : "—" },
    { k: "EPS", v: eps != null ? `$${eps.toFixed(2)}` : "—" },
    { k: "Beta", v: n("beta") != null ? n("beta")!.toFixed(2) : "—" },
    { k: "Div yield", v: dy != null ? `${dy.toFixed(2)}%` : "—" },
    { k: "52W high", v: n("52WeekHigh") != null ? money(n("52WeekHigh")!) : "—" },
    { k: "52W low", v: n("52WeekLow") != null ? money(n("52WeekLow")!) : "—" },
    { k: "Gross margin", v: n("grossMarginTTM") != null ? `${n("grossMarginTTM")!.toFixed(1)}%` : "—" },
  ];
}

/** Stat-tile label/value for the 4th hero tile: prefer P/E, fall back to the
    52-week trading range, then a dash. Needs the fetched detail (fundamentals). */
export function statPE(d: StockDetailData | null): { label: string; value: string } {
  if (!d) return { label: "P/E", value: "—" };
  const f = d.fundamentals || {};
  const n = (k: string): number | null =>
    typeof f[k] === "number" ? (f[k] as number) : null;
  const pe = n("peTTM") ?? n("peBasicExclExtraTTM");
  if (pe != null) return { label: "P/E", value: pe.toFixed(1) };
  const lo = n("52WeekLow");
  const hi = n("52WeekHigh");
  if (lo != null && hi != null) return { label: "52-wk range", value: `${money(lo)} – ${money(hi)}` };
  return { label: "P/E", value: "—" };
}
