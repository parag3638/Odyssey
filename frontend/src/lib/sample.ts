/* Deterministic sample data for the styleguide and for showcase components
   whose live data isn't wired yet (charts, news, earnings, screener).
   Everything here is sample — clearly labelled as such in the UI. */

import type {
  AccountListItem,
  AllocationSlice,
  EarningsDay,
  HoldingView,
  KpiItem,
  NewsItem,
  ScreenerRow,
} from "./types";

/** Deterministic price-ish series (no Math.random → no hydration mismatch). */
export function makeSeries(
  n = 120,
  start = 100,
  drift = 0.4,
  seed = 1,
): number[] {
  const out: number[] = [];
  let p = start;
  for (let i = 0; i < n; i++) {
    p +=
      Math.sin((i + seed) / 8) * 3 +
      Math.cos((i + seed) / 3) * 1.3 +
      (i / n) * drift +
      (((i * 61 + seed) % 13) - 6) / 4;
    out.push(p);
  }
  return out;
}

export const sampleHeroSeries = makeSeries(160, 120, 9, 3);
export const samplePerfSeries = makeSeries(150, 70, 2, 7);
export const sampleDetailSeries = makeSeries(170, 300, 14, 5);

export const sampleHoldings: HoldingView[] = [
  { symbol: "TSLA", name: "Tesla, Inc.", qty: 10, avgCost: 317.2, price: 330.55, prevClose: 324.7, account: "Trailing", tag: "Trailing" },
  { symbol: "NVDA", name: "NVIDIA Corp.", qty: 4, avgCost: 168.4, price: 173.41, prevClose: 173.81, account: "Liquid fund", tag: "Copy" },
  { symbol: "GOOGL", name: "Alphabet Inc.", qty: 6, avgCost: 182.1, price: 191.3, prevClose: 190.35, account: "Liquid fund", tag: "Copy" },
  { symbol: "AMZN", name: "Amazon.com", qty: 3, avgCost: 211.05, price: 224.18, prevClose: 221.74, account: "Retirement", tag: "Copy" },
  { symbol: "MSFT", name: "Microsoft", qty: 2, avgCost: 498.6, price: 512.04, prevClose: 508.99, account: "Retirement", tag: "Copy" },
  { symbol: "AAPL", name: "Apple Inc.", qty: 12, avgCost: 221.4, price: 233.45, prevClose: 234.6, account: "Liquid fund", tag: "Manual" },
];

export const sampleAllocation: AllocationSlice[] = [
  { label: "Technology", value: 9650, color: "#5b8def" },
  { label: "Cons. cyclical", value: 4205, color: "#8b7bf0" },
  { label: "Comm. services", value: 2295, color: "#34d399" },
  { label: "Cash", value: 1420, color: "#6a6a72" },
];

export const sampleStats = [
  { pv: "+$4,831.63", pk: "Total return", cls: "pos" as const, hl: true },
  { pv: "−$31.29", pk: "Realized return", cls: "neg" as const },
  { pv: "$1,219.98", pk: "Dividends earned", cls: "" as const },
  { pv: "$50,000.00", pk: "Net deposits", cls: "" as const },
];

export const sampleKpis: KpiItem[] = [
  { k: "Mkt cap", v: "$1.05T" },
  { k: "P/E ratio", v: "71.3" },
  { k: "EV/Sales", v: "9.8" },
  { k: "FY Revenue", v: "$97.6B" },
  { k: "EPS", v: "$4.30" },
  { k: "Gross margin", v: "17.9%" },
  { k: "Beta", v: "2.31" },
  { k: "Sector", v: "Cons Cyc" },
];

export const sampleNews: NewsItem = {
  symbol: "TSLA",
  name: "Tesla",
  color: "#e2433a",
  time: "Today · 1 hour ago",
  text: "Tesla shares dropped after Elon Musk's clash with Donald Trump, netting short sellers $4B. The dip follows a downgrade and concerns over Tesla's political entanglements.",
  side: "sell",
  perfLabel: "TSLA Market sell",
  perfValue: 13245.65,
  perfPct: -33.68,
  account: "TFSA",
  date: "December 26, 2024",
  series: makeSeries(120, 200, -6, 11),
  splitAt: 60,
};

export const sampleEarnings: EarningsDay[] = [
  { dow: "FRI", dnum: 13, events: 0, logos: [] },
  { dow: "MON", dnum: 16, ago: "4 days ago", events: 3, today: true, logos: [{ label: "E", color: "#16a34a" }, { label: "W", color: "#0a66c2" }, { label: "K", color: "#e2433a" }] },
  { dow: "TUE", dnum: 17, events: 2, logos: [{ label: "W", color: "#4285f4" }, { label: "B", color: "#1d4ed8" }] },
  { dow: "WED", dnum: 18, events: 2, logos: [{ label: "K", color: "#16a34a" }, { label: "C", color: "#0891b2" }] },
  { dow: "THU", dnum: 19, events: 0, logos: [] },
  { dow: "FRI", dnum: 20, events: 1, logos: [{ label: "V", color: "#7c3aed" }] },
  { dow: "MON", dnum: 23, events: 0, logos: [] },
];

export const sampleScreener: ScreenerRow[] = [
  { symbol: "DASH", name: "DoorDash, Inc.", sector: "Comm Serv", rev: "11.24B", revYoY: 23.35, pe: 239.23, eps: 0.76, evSales: 6.92, mktCap: "77.72B", ret1d: 3.34 },
  { symbol: "SE", name: "Sea Limited", sector: "Cons Cyc", rev: "16.81B", revYoY: 28.46, pe: 195.42, eps: 0.75, evSales: 5.25, mktCap: "86.55B", ret1d: 2.09 },
  { symbol: "CPNG", name: "Coupang, Inc.", sector: "Cons Cyc", rev: "31.06B", revYoY: 20.88, pe: 185.32, eps: 0.14, evSales: 1.47, mktCap: "46.87B", ret1d: -3.5 },
  { symbol: "CVNA", name: "Carvana Co.", sector: "Cons Cyc", rev: "13.67B", revYoY: 26.94, pe: 181.6, eps: 1.57, evSales: 4.85, mktCap: "62.08B", ret1d: 10.32 },
  { symbol: "NOW", name: "ServiceNow, Inc.", sector: "Tech", rev: "11.47B", revYoY: 21.01, pe: 132.28, eps: 7.37, evSales: 17.64, mktCap: "203.35B", ret1d: -0.69 },
  { symbol: "SPOT", name: "Spotify Technology", sector: "Comm Serv", rev: "17.41B", revYoY: 16.07, pe: 108.01, eps: 6.07, evSales: 7.07, mktCap: "132.24B", ret1d: -0.49 },
  { symbol: "AVGO", name: "Broadcom Inc.", sector: "Tech", rev: "54.53B", revYoY: 40.3, pe: 100.21, eps: 2.07, evSales: 19.12, mktCap: "987.13B", ret1d: 1.44 },
  { symbol: "LLY", name: "Eli Lilly and Company", sector: "Healthcare", rev: "49B", revYoY: 36.38, pe: 62.25, eps: 12.07, evSales: 15.39, mktCap: "718.08B", ret1d: -3.12 },
];

export const sampleAccounts: AccountListItem[] = [
  { name: "Trading account", sub: "TFSA", value: 16543.21, color: "var(--tile-red)", icon: "lightning" },
  { name: "Diamond hands", sub: "RRSP", value: 9876.54, color: "var(--tile-amber)", icon: "diamond" },
  { name: "Wealthsimple", sub: "Group RRSP", value: 4233.25, color: "var(--tile-violet)", icon: "briefcase" },
];
