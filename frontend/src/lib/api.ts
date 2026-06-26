const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export interface Position {
  symbol: string;
  qty: number;
  avg_entry_price: number;
}

export interface OrderOut {
  id: string;
  symbol: string;
  side: string;
  qty: number;
  status: string;
  reason: string;
}

export interface AccountOut {
  id: string;
  label: string;
  mode: string;
  masked_secret: string;
}

export type OrderSide = "buy" | "sell";

/* ──────────────────────────────────────────────────────────────────────────
   Backend-or-snapshot data layer.

   The app tries the real backend; if it's unreachable (e.g. the frontend is
   deployed standalone with no API running), it falls back to a static snapshot
   bundled at /snapshot.json and runs read-only in "demo mode". A small delay in
   demo mode lets the existing loading skeletons show so it still feels live.
   ────────────────────────────────────────────────────────────────────────── */

let _backendUp: Promise<boolean> | null = null;
function backendUp(): Promise<boolean> {
  if (!_backendUp) {
    _backendUp = (async () => {
      try {
        const ctrl = new AbortController();
        const t = setTimeout(() => ctrl.abort(), 2000);
        const r = await fetch(`${API_BASE}/health`, { cache: "no-store", signal: ctrl.signal });
        clearTimeout(t);
        return r.ok;
      } catch {
        return false;
      }
    })();
  }
  return _backendUp;
}

/** True when running off the static snapshot (backend offline). */
export async function isDemoMode(): Promise<boolean> {
  return !(await backendUp());
}

interface Snapshot {
  exact: Record<string, unknown>;
  stocks: StockRow[];
  metrics: Record<string, StockMetrics>;
  detail: Record<string, StockDetailData>;
  bots: Record<string, unknown>;
  signals: Signal[];
  activity: Activity[];
}
let _snap: Promise<Snapshot | null> | null = null;
function snapshot(): Promise<Snapshot | null> {
  if (!_snap) {
    _snap = fetch("/snapshot.json", { cache: "force-cache" })
      .then((r) => (r.ok ? (r.json() as Promise<Snapshot>) : null))
      .catch(() => null);
  }
  return _snap;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const qparams = (path: string) => new URLSearchParams(path.split("?")[1] ?? "");

/** Resolve a GET path from the snapshot. Returns undefined when uncached. */
function fromSnapshot(snap: Snapshot, path: string): unknown {
  if (path in snap.exact) return snap.exact[path];

  if (path.startsWith("/activity")) {
    const limit = Number(qparams(path).get("limit") ?? 50);
    return snap.activity.slice(0, limit);
  }
  if (path.startsWith("/stocks/metrics")) {
    const syms = qparams(path).get("symbols")?.split(",").filter(Boolean) ?? [];
    const out: Record<string, StockMetrics> = {};
    for (const s of syms) if (snap.metrics[s]) out[s] = snap.metrics[s];
    return out;
  }
  // history isn't snapshotted → empty (the chart shows its illustrative fallback)
  if (/^\/stocks\/[^/]+\/history/.test(path)) return [];
  let m = path.match(/^\/stocks\/([^/]+)\/signals$/);
  if (m) return snap.signals.filter((s) => s.symbol.toUpperCase() === m![1].toUpperCase());
  if (/^\/stocks\/[^/]+\/(news|earnings|analysis|dividends)$/.test(path)) return [];
  m = path.match(/^\/stocks\/([^/?]+)$/);
  if (m && m[1] !== "industries" && m[1] !== "movers") return snap.detail[m[1]];
  if (path.startsWith("/stocks")) {
    const qs = qparams(path);
    let rows = snap.stocks;
    const sector = qs.get("sector");
    const industry = qs.get("industry");
    const q = qs.get("q");
    if (sector) rows = rows.filter((r) => r.sector === sector);
    if (industry) rows = rows.filter((r) => r.industry === industry);
    if (q) {
      const u = q.toUpperCase();
      rows = rows.filter((r) => r.symbol.includes(u) || r.name.toUpperCase().includes(u));
    }
    const limit = Number(qs.get("limit") ?? rows.length);
    return rows.slice(0, limit);
  }
  m = path.match(/^\/bots\/(\d+)$/);
  if (m) return snap.bots[m[1]];
  if (path.startsWith("/signals")) {
    const pol = qparams(path).get("politician");
    return pol
      ? snap.signals.filter((s) => s.politician.toLowerCase().includes(pol.toLowerCase()))
      : snap.signals;
  }
  return undefined;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase();

  if (await backendUp()) {
    const res = await fetch(`${API_BASE}${path}`, {
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      ...init,
    });
    if (res.ok) return res.json() as Promise<T>;
    if (method === "GET") {
      const snap = await snapshot();
      const cached = snap ? fromSnapshot(snap, path) : undefined;
      if (cached !== undefined) return cached as T;
    }
    let detail: string | undefined;
    try {
      const body = await res.json();
      detail =
        typeof body?.detail === "string"
          ? body.detail
          : body?.detail
            ? JSON.stringify(body.detail)
            : undefined;
    } catch {
      // body wasn't JSON — fall through to status text
    }
    throw new Error(detail ?? `Request failed (${res.status})`);
  }

  // ── demo mode: backend offline, serve the snapshot ──
  if (method === "GET") {
    const snap = await snapshot();
    const cached = snap ? fromSnapshot(snap, path) : undefined;
    if (cached !== undefined) {
      await sleep(280 + Math.floor(Math.random() * 360)); // let the skeletons breathe
      return cached as T;
    }
    await sleep(200);
    throw new Error("Offline demo — no cached data for this view.");
  }
  await sleep(150);
  throw new Error("Demo mode — connect the backend to place trades or create bots.");
}

export function getHealth(): Promise<unknown> {
  return request<unknown>("/health");
}

export function listAccounts(): Promise<AccountOut[]> {
  return request<AccountOut[]>("/accounts");
}

export function getPositions(accountId: string): Promise<Position[]> {
  return request<Position[]>(`/positions/${accountId}`);
}

export interface QuoteOut {
  symbol: string;
  price: number;
  prev_close: number | null;
}

/** Live quotes for the given symbols, or (omit) the account's holdings. */
export function getQuotes(
  accountId: string,
  symbols?: string[],
): Promise<QuoteOut[]> {
  const q = symbols && symbols.length ? `?symbols=${symbols.join(",")}` : "";
  return request<QuoteOut[]>(`/positions/${accountId}/quotes${q}`);
}

export interface AccountSummary {
  cash: number | null;
}

export function getAccountSummary(accountId: string): Promise<AccountSummary> {
  return request<AccountSummary>(`/positions/${accountId}/summary`);
}

export function placeOrder(
  accountId: string,
  symbol: string,
  qty: number,
  side: OrderSide = "buy",
): Promise<OrderOut> {
  return request<OrderOut>("/orders", {
    method: "POST",
    body: JSON.stringify({ account_id: accountId, symbol, qty, side }),
  });
}

// ============ bots ============

export type Bot = {
  id: number;
  name: string;
  strategy_type: string;
  status: string;
  config: Record<string, unknown>;
  schedule_cadence_sec: number;
};
export type BotDetail = Bot & {
  position: {
    symbol: string;
    qty: number;
    avg_entry_price: number | null;
    stop_floor: number | null;
    triggered_rungs: number[];
  } | null;
  recent_activity: {
    event: string;
    level: string;
    detail: Record<string, unknown>;
  }[];
};

export async function listBots(): Promise<Bot[]> {
  return request<Bot[]>("/bots");
}
export async function getBot(id: number): Promise<BotDetail> {
  return request<BotDetail>(`/bots/${id}`);
}
export type Signal = {
  id: number;
  politician: string;
  symbol: string;
  tx_type: string;
  tx_date: string;
  disclosed_date: string;
  amount_range: string;
  source_url: string;
};

export async function listSignals(politician?: string): Promise<Signal[]> {
  const q = politician ? `?politician=${encodeURIComponent(politician)}` : "";
  return request<Signal[]>(`/signals${q}`);
}

export async function syncSignals(): Promise<{ added: number }> {
  return request<{ added: number }>("/signals/sync", { method: "POST" });
}

export async function createBot(input: {
  name: string;
  account_id: number;
  strategy_type?: string;
  symbol?: string;
  initial_shares?: number;
  stop_pct?: number;
  trail_pct?: number;
  politician?: string;
  per_trade_notional?: number;
}): Promise<Bot> {
  return request<Bot>("/bots", { method: "POST", body: JSON.stringify(input) });
}
export async function runBot(
  id: number,
): Promise<{
  price: number;
  actions: number;
  stop_floor: number | null;
  notes: string[];
}> {
  return request<{ price: number; actions: number; stop_floor: number | null; notes: string[] }>(
    `/bots/${id}/run`,
    { method: "POST" },
  );
}
export async function setBotStatus(id: number, status: string): Promise<Bot> {
  return request<Bot>(`/bots/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  });
}

// ============ activity ============

export type Activity = {
  id: number;
  bot_id: number | null;
  level: string;
  event: string;
  detail: Record<string, unknown>;
  created_at: string | null;
  bot_name: string | null;
  symbol: string | null;
};

export async function listActivity(limit = 50): Promise<Activity[]> {
  return request<Activity[]>(`/activity?limit=${limit}`);
}

// ============ stocks (research) ============

export interface StockRow {
  symbol: string;
  name: string;
  sector: string;
  industry: string;
  logo_url: string;
  market_cap: number | null;
  price: number | null;
  change: number | null;
  change_pct: number | null;
}
export interface StockDetailData extends StockRow {
  exchange: string;
  prev_close: number | null;
  fundamentals: Record<string, number | string | null> | null;
}
export interface StockMetrics {
  pe: number | null;
  eps: number | null;
  revenue: number | null; // absolute TTM revenue in USD
  revYoY: number | null; // YoY revenue growth, percent
  evSales: number | null;
  marketCap: number | null; // absolute market cap in USD
  earnings: string | null; // next-earnings status, e.g. "Pending"
}
export type StockMetricsMap = Record<string, StockMetrics>;

/** Fundamentals for a batch of symbols (current page only). Each symbol is
    cached 6h server-side; degrades to nulls without a Finnhub key. */
export function getStockMetrics(symbols: string[]): Promise<StockMetricsMap> {
  if (symbols.length === 0) return Promise.resolve({});
  const qs = new URLSearchParams({ symbols: symbols.join(",") }).toString();
  return request<StockMetricsMap>(`/stocks/metrics?${qs}`);
}

export interface IndustryRow {
  industry: string;
  sector: string;
  count: number;
}
export interface HistoryPoint {
  t: string;
  price: number;
  volume: number;
}
export interface NewsArticle {
  headline: string;
  summary: string;
  source: string;
  url: string;
  datetime: string | null;
  image: string;
  symbols: string[];
}
export interface EarningsPoint {
  period: string;
  actual: number | null;
  estimate: number | null;
}
export interface RecommendationPoint {
  period: string;
  strongBuy?: number;
  buy?: number;
  hold?: number;
  sell?: number;
  strongSell?: number;
}
export interface DividendPoint {
  ex_date: string;
  amount: number;
  pay_date: string;
}
export interface MoverItem {
  symbol: string;
  price: number;
  change_pct: number;
}
export interface Movers {
  gainers: MoverItem[];
  losers: MoverItem[];
}

export function listStocks(params?: {
  industry?: string;
  sector?: string;
  q?: string;
  sort?: string;
  limit?: number;
}): Promise<StockRow[]> {
  const u = new URLSearchParams();
  if (params?.industry) u.set("industry", params.industry);
  if (params?.sector) u.set("sector", params.sector);
  if (params?.q) u.set("q", params.q);
  if (params?.sort) u.set("sort", params.sort);
  if (params?.limit) u.set("limit", String(params.limit));
  const qs = u.toString();
  return request<StockRow[]>(`/stocks${qs ? `?${qs}` : ""}`);
}
export function getStockIndustries(): Promise<IndustryRow[]> {
  return request<IndustryRow[]>("/stocks/industries");
}
export function getStock(symbol: string): Promise<StockDetailData> {
  return request<StockDetailData>(`/stocks/${symbol}`);
}
export function getStockHistory(symbol: string, range = "1M"): Promise<HistoryPoint[]> {
  return request<HistoryPoint[]>(`/stocks/${symbol}/history?range=${range}`);
}
export function getStockNews(symbol: string): Promise<NewsArticle[]> {
  return request<NewsArticle[]>(`/stocks/${symbol}/news`);
}
export function getStockEarnings(symbol: string): Promise<EarningsPoint[]> {
  return request<EarningsPoint[]>(`/stocks/${symbol}/earnings`);
}
export function getStockAnalysis(symbol: string): Promise<RecommendationPoint[]> {
  return request<RecommendationPoint[]>(`/stocks/${symbol}/analysis`);
}
export function getStockDividends(symbol: string): Promise<DividendPoint[]> {
  return request<DividendPoint[]>(`/stocks/${symbol}/dividends`);
}
export function getStockSignals(symbol: string): Promise<Signal[]> {
  return request<Signal[]>(`/stocks/${symbol}/signals`);
}
export function getMovers(): Promise<Movers> {
  return request<Movers>("/stocks/movers");
}
