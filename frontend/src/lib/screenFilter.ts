import type { ScreenerFilter, StockMetrics, StockMetricsMap, StockRow } from "@/lib/api";

/* Deterministic filter engine for the natural-language screener.

   The LLM only *parses* a sentence into these criteria — this module runs them.
   Fields on StockRow (sector/industry/marketCap/change_pct) resolve directly;
   fundamentals (pe/eps/revenue/revYoY/evSales) need the per-symbol metrics call. */

const METRIC_FIELDS = ["pe", "eps", "revenue", "revYoY", "evSales"] as const;

/** True when any criterion needs the fundamentals batch (not on StockRow). */
export function needsMetrics(filters: ScreenerFilter[], sortField?: string | null): boolean {
  const fields = [...filters.map((f) => f.field), ...(sortField ? [sortField] : [])];
  return fields.some((f) => (METRIC_FIELDS as readonly string[]).includes(f));
}

function valueOf(
  row: StockRow,
  metrics: StockMetrics | undefined,
  field: string,
): number | string | null {
  switch (field) {
    case "sector":
      return row.sector;
    case "industry":
      return row.industry;
    case "change_pct":
      return row.change_pct;
    case "marketCap":
      return row.market_cap ?? metrics?.marketCap ?? null;
    case "pe":
      return metrics?.pe ?? null;
    case "eps":
      return metrics?.eps ?? null;
    case "revenue":
      return metrics?.revenue ?? null;
    case "revYoY":
      return metrics?.revYoY ?? null;
    case "evSales":
      return metrics?.evSales ?? null;
    default:
      return null;
  }
}

function passes(actual: number | string | null, op: string, raw: string): boolean {
  if (actual === null || actual === undefined) return false; // unknown never matches
  if (typeof actual === "string") {
    const a = actual.toLowerCase();
    const b = raw.trim().toLowerCase();
    return op === "=" ? a === b : a.includes(b);
  }
  const target = Number(String(raw).replace(/[$,%\s,]/g, ""));
  if (!Number.isFinite(target)) return true; // unparseable threshold → don't filter
  switch (op) {
    case "<":
      return actual < target;
    case "<=":
      return actual <= target;
    case ">":
      return actual > target;
    case ">=":
      return actual >= target;
    case "=":
      return actual === target;
    default:
      return true;
  }
}

export function applyScreen(
  rows: StockRow[],
  filters: ScreenerFilter[],
  metrics: StockMetricsMap,
  sortField?: string | null,
  sortDir: string = "desc",
): StockRow[] {
  let out = rows.filter((r) =>
    filters.every((f) => passes(valueOf(r, metrics[r.symbol], f.field), f.op, f.value)),
  );
  if (sortField) {
    const dir = sortDir === "asc" ? 1 : -1;
    out = out.slice().sort((a, b) => {
      const va = valueOf(a, metrics[a.symbol], sortField);
      const vb = valueOf(b, metrics[b.symbol], sortField);
      const na = typeof va === "number" ? va : null;
      const nb = typeof vb === "number" ? vb : null;
      if (na === null && nb === null) return 0;
      if (na === null) return 1; // unknowns last, regardless of direction
      if (nb === null) return -1;
      return (na - nb) * dir;
    });
  }
  return out;
}

const LABELS: Record<string, string> = {
  pe: "P/E",
  eps: "EPS",
  revenue: "Revenue",
  revYoY: "Rev. Y/Y",
  evSales: "EV/Sales",
  marketCap: "Mkt cap",
  change_pct: "1D return",
  sector: "Sector",
  industry: "Industry",
};

const compact = (n: number): string => {
  const abs = Math.abs(n);
  if (abs >= 1e12) return `$${(n / 1e12).toFixed(1)}T`;
  if (abs >= 1e9) return `$${(n / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
  return String(n);
};

/** Human-readable chip text, e.g. "Revenue > $10.0B". */
export function chipLabel(f: ScreenerFilter): string {
  const name = LABELS[f.field] ?? f.field;
  const num = Number(String(f.value).replace(/[$,%\s,]/g, ""));
  if (f.op === "contains" || f.op === "=") {
    if (!Number.isFinite(num)) return `${name}: ${f.value}`;
  }
  if (!Number.isFinite(num)) return `${name} ${f.op} ${f.value}`;
  if (f.field === "marketCap" || f.field === "revenue") return `${name} ${f.op} ${compact(num)}`;
  if (f.field === "revYoY" || f.field === "change_pct") return `${name} ${f.op} ${num}%`;
  return `${name} ${f.op} ${num}`;
}

export function sortLabel(field: string, dir: string): string {
  return `Sort: ${LABELS[field] ?? field} ${dir === "asc" ? "↑" : "↓"}`;
}
