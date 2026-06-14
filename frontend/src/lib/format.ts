/* Number & money formatting for the Fey UI.
   Uses a real minus sign (−, U+2212) for negatives so figures align and read
   like the references. Cents are returned separately where the design dims them. */

const MINUS = "−";

export function money(v: number, dp = 2): string {
  const sign = v < 0 ? MINUS : "";
  return `${sign}$${Math.abs(v).toLocaleString("en-US", {
    minimumFractionDigits: dp,
    maximumFractionDigits: dp,
  })}`;
}

export function signedMoney(v: number, dp = 2): string {
  const sign = v < 0 ? MINUS : "+";
  return `${sign}$${Math.abs(v).toLocaleString("en-US", {
    minimumFractionDigits: dp,
    maximumFractionDigits: dp,
  })}`;
}

/** Split a value into sign / whole / cents so the UI can dim the cents. */
export function splitMoney(v: number): {
  sign: string;
  whole: string;
  cents: string;
} {
  const sign = v < 0 ? MINUS : "";
  const [whole, cents] = Math.abs(v)
    .toLocaleString("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })
    .split(".");
  return { sign, whole, cents };
}

export function pct(v: number, signed = true, dp = 2): string {
  const sign = v < 0 ? MINUS : signed ? "+" : "";
  return `${sign}${Math.abs(v).toFixed(dp)}%`;
}

/** Compact money for KPIs: $1.05T, $97.6B, $250K. */
export function compact(v: number): string {
  const a = Math.abs(v);
  const sign = v < 0 ? MINUS : "";
  if (a >= 1e12) return `${sign}$${(a / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `${sign}$${(a / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(2)}M`;
  if (a >= 1e3) return `${sign}$${(a / 1e3).toFixed(1)}K`;
  return `${sign}$${a.toFixed(2)}`;
}

/** Compact magnitude without a currency sign: 1.05T, 97.6B, 11.24B, 250K.
    Used for screener Rev. / Mkt cap columns where the design omits the "$". */
export function compactNum(v: number): string {
  const a = Math.abs(v);
  const sign = v < 0 ? MINUS : "";
  const f = (x: number) => x.toLocaleString("en-US", { maximumFractionDigits: 2 });
  if (a >= 1e12) return `${sign}${f(a / 1e12)}T`;
  if (a >= 1e9) return `${sign}${f(a / 1e9)}B`;
  if (a >= 1e6) return `${sign}${f(a / 1e6)}M`;
  if (a >= 1e3) return `${sign}${f(a / 1e3)}K`;
  return `${sign}${f(a)}`;
}

export function signClass(v: number): "pos" | "neg" | "" {
  return v > 0 ? "pos" : v < 0 ? "neg" : "";
}

export function qtyFmt(q: number): string {
  return Number.isInteger(q)
    ? String(q)
    : q.toLocaleString("en-US", { maximumFractionDigits: 4 });
}

export function initials(label: string): string {
  const parts = label.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "··";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

/* Deterministic logo color from a symbol (stable across renders, no hydration
   mismatch). Mirrors the brand-ish palette in the references. */
const LOGO_COLORS = [
  "#e2433a",
  "#76b900",
  "#4285f4",
  "#ff9900",
  "#0a66c2",
  "#8b5cf6",
  "#14b8a6",
  "#ef4444",
  "#22c55e",
  "#eab308",
];

export function logoColor(symbol: string): string {
  let hash = 0;
  for (let i = 0; i < symbol.length; i++) {
    hash = (hash * 31 + symbol.charCodeAt(i)) >>> 0;
  }
  return LOGO_COLORS[hash % LOGO_COLORS.length];
}

/* Real company logo by ticker (free, symbol-keyed CDN). Falls back to a
   letter-badge in TickerLogo if the image 404s. Finnhub's logo_url, when
   present, takes priority over this. */
export function logoUrl(symbol: string): string {
  return `https://assets.parqet.com/logos/symbol/${encodeURIComponent(symbol)}?format=png`;
}
