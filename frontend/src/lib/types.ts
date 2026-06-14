/* Shared view-model types used by the UI component library and sample data.
   These are presentation shapes — the API layer maps live data into them. */

export interface QuoteInfo {
  symbol: string;
  price: number;
  prev_close?: number | null;
}
export type QuoteMap = Record<string, QuoteInfo>;

/** A holding ready to render in the holdings table. */
export interface HoldingView {
  symbol: string;
  name?: string;
  qty: number;
  avgCost: number;
  price?: number | null; // current price (live quote)
  prevClose?: number | null; // previous close → today's return
  account?: string; // account-type pill, e.g. "Liquid fund"
  tag?: string; // bot tag, e.g. "Trailing"
  color?: string; // logo color override
}

export interface AllocationSlice {
  label: string;
  value: number;
  color: string;
}

export interface KpiItem {
  k: string;
  v: string;
}

export interface NewsItem {
  symbol: string;
  name?: string;
  color?: string;
  time: string;
  text: string;
  side?: "buy" | "sell";
  perfLabel?: string; // e.g. "TSLA Market sell"
  perfValue?: number; // e.g. 13245.65
  perfPct?: number; // signed, e.g. -33.68
  account?: string;
  date?: string;
  series?: number[];
  splitAt?: number; // index where past→now split happens
}

export interface EarningsLogo {
  label: string;
  color: string;
}
export interface EarningsDay {
  dow: string;
  dnum: number;
  ago?: string;
  events: number;
  logos: EarningsLogo[];
  today?: boolean;
}

export interface ScreenerRow {
  symbol: string;
  name: string;
  sector: string;
  rev: string;
  revYoY: number;
  pe: number;
  eps: number;
  evSales: number;
  mktCap: string;
  ret1d: number;
}

export interface AccountListItem {
  name: string;
  sub: string;
  value: number;
  color: string; // icon-tile hue
  icon: "lightning" | "diamond" | "briefcase" | "bank" | "dollar";
}
