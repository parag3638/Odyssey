const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "https://backend--odyssey--grmgc87s4dxd.code.run";

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

let _backendReachable: boolean | null = null;

/** True when running off the static snapshot (backend offline). */
export async function isDemoMode(): Promise<boolean> {
  if (_backendReachable !== null) return !_backendReachable;
  try {
    const response = await fetch(`${API_BASE}/health`, { cache: "no-store" });
    _backendReachable = response.ok;
  } catch {
    _backendReachable = false;
  }
  return !_backendReachable;
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

const qparams = (path: string) => new URLSearchParams(path.split("?")[1] ?? "");

/** Resolve a GET path from the snapshot. Returns undefined when uncached. */
function fromSnapshot(snap: Snapshot, path: string): unknown {
  if (path in snap.exact) return snap.exact[path];

  if (path === "/dashboard/bootstrap") {
    const accounts = (snap.exact["/accounts"] as AccountOut[] | undefined) ?? [];
    const movers = (snap.exact["/stocks/movers"] as Movers | undefined) ?? {
      gainers: [],
      losers: [],
    };
    const stocks = [...snap.stocks]
      .sort((a, b) => (b.market_cap ?? 0) - (a.market_cap ?? 0))
      .slice(0, 8);
    return {
      account: accounts[0] ?? null,
      stocks,
      movers,
      bots: (snap.exact["/bots"] as Bot[] | undefined) ?? [],
      signals: snap.signals.slice(0, 8),
      featured_symbol: stocks[0]?.symbol ?? "",
    } satisfies DashboardBootstrap;
  }

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

// Hard ceiling on any single request. Without this, a request that hangs at
// the network layer (dead connection that never completes, rather than a
// clean error) leaves its promise permanently pending — callers that track
// "already requested" symbols (e.g. StockFinderTable's metrics fetch) then
// never retry, since neither .then() nor .catch() ever fires. 20s is
// generous — cold-cache fundamentals fetches have been observed taking up
// to ~19s in degraded conditions — but finite, so a stuck request always
// eventually fails and unblocks a retry instead of hanging forever.
const REQUEST_TIMEOUT_MS = 20_000;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase();
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...init,
      signal: ctrl.signal,
    });
    _backendReachable = true;
  } catch (e) {
    _backendReachable = false;
    if (method === "GET") {
      const snap = await snapshot();
      const cached = snap ? fromSnapshot(snap, path) : undefined;
      if (cached !== undefined) return cached as T;
    }
    if (e instanceof DOMException && e.name === "AbortError") {
      throw new Error("Request timed out — the server took too long to respond.");
    }
    throw new Error("Could not reach the backend.");
  } finally {
    clearTimeout(timer);
  }
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

export function getHealth(): Promise<unknown> {
  return request<unknown>("/health");
}

export function listAccounts(): Promise<AccountOut[]> {
  return request<AccountOut[]>("/accounts");
}

export function createAccount(input: {
  label: string;
  alpaca_key_id: string;
  alpaca_secret: string;
  endpoint?: string;
}): Promise<AccountOut> {
  return request<AccountOut>("/accounts", { method: "POST", body: JSON.stringify(input) });
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

/* ── AI advisory layer ──────────────────────────────────────────────────────
   Grounded, source-cited summaries/chat. Every call is best-effort: it resolves
   to `available:false` (never throws) when the backend is offline or the LLM key
   is unset, so callers simply hide the AI affordance. */

export interface Citation {
  id: string;
  label: string;
  url: string;
  kind: string;
}

export interface AiResponse {
  available: boolean;
  text: string | null;
  citations: Citation[];
  disclaimer: string;
  model: string | null;
}

const AI_UNAVAILABLE: AiResponse = {
  available: false,
  text: null,
  citations: [],
  disclaimer: "",
  model: null,
};

/** Plain-English, source-cited summary of a stock's recent news + analyst views. */
export async function getAiSummary(symbol: string): Promise<AiResponse> {
  try {
    return await request<AiResponse>(`/ai/stocks/${symbol}/summary`);
  } catch {
    return AI_UNAVAILABLE;
  }
}

export interface BullBearOut {
  available: boolean;
  bull: string[];
  bear: string[];
  crux: string | null;
  citations: Citation[];
  disclaimer: string;
  model: string | null;
}

const BULLBEAR_UNAVAILABLE: BullBearOut = {
  available: false,
  bull: [],
  bear: [],
  crux: null,
  citations: [],
  disclaimer: "",
  model: null,
};

/** Bull-vs-bear synthesis for a stock (news vs ratings vs fundamentals). */
export async function getBullBear(symbol: string): Promise<BullBearOut> {
  try {
    return await request<BullBearOut>(`/ai/stocks/${symbol}/bull-bear`);
  } catch {
    return BULLBEAR_UNAVAILABLE;
  }
}

/** Descriptive AI read of a stock's congressional trades (cluster + prose). */
export async function getCongressContext(symbol: string): Promise<AiResponse> {
  try {
    return await request<AiResponse>(`/ai/signals/${symbol}/context`);
  } catch {
    return AI_UNAVAILABLE;
  }
}

export interface Concentration {
  label: string;
  weight_pct: number;
}

export interface PortfolioHealthOut {
  available: boolean;
  text: string | null;
  concentrations: Concentration[];
  disclaimer: string;
  model: string | null;
}

/** Concentration/overlap observer over the connected account's holdings. */
export async function getPortfolioHealth(): Promise<PortfolioHealthOut> {
  const off: PortfolioHealthOut = {
    available: false,
    text: null,
    concentrations: [],
    disclaimer: "",
    model: null,
  };
  try {
    return await request<PortfolioHealthOut>("/ai/portfolio/health");
  } catch {
    return off;
  }
}

/** Plain-English explanation of a risk rejection the engine already produced. */
export async function explainRisk(input: {
  reason: string;
  symbol?: string;
  qty?: number;
  side?: string;
}): Promise<AiResponse> {
  try {
    return await request<AiResponse>("/ai/risk/explain", {
      method: "POST",
      body: JSON.stringify({ side: "buy", symbol: "", qty: 0, ...input }),
    });
  } catch {
    return AI_UNAVAILABLE;
  }
}

export interface ScreenerFilter {
  field: string;
  op: string;
  value: string;
}

export interface ScreenerParseOut {
  available: boolean;
  filters: ScreenerFilter[];
  sort_field: string | null;
  sort_dir: string;
  note: string | null;
  model: string | null;
}

/** Translate a plain-English screen into structured filters (parse only). */
export async function parseScreen(query: string): Promise<ScreenerParseOut> {
  const off: ScreenerParseOut = {
    available: false,
    filters: [],
    sort_field: null,
    sort_dir: "desc",
    note: null,
    model: null,
  };
  try {
    return await request<ScreenerParseOut>("/ai/screener/parse", {
      method: "POST",
      body: JSON.stringify({ query }),
    });
  } catch {
    return off;
  }
}

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
}

/** Stream a grounded assistant reply. Calls `onToken` for each chunk as it arrives
 *  and resolves with the final text + citations. Falls back to the non-streaming
 *  /ai/chat endpoint in demo mode or on any error, so it never throws. */
export async function streamAssistant(
  messages: ChatTurn[],
  context: { symbol?: string | null },
  onToken: (t: string) => void,
  signal?: AbortSignal,
): Promise<{ text: string; citations: Citation[] }> {
  const payload = JSON.stringify({ messages, context });

  try {
    const res = await fetch(`${API_BASE}/ai/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: payload,
        signal,
    });
    if (res.ok && res.body) {
      _backendReachable = true;
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";
        let text = "";
        let citations: Citation[] = [];
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          const parts = buf.split("\n\n");
          buf = parts.pop() ?? ""; // keep the last (possibly partial) frame
          for (const part of parts) {
            const line = part.trim();
            if (!line.startsWith("data:")) continue;
            const data = line.slice(5).trim();
            if (data === "[DONE]") continue;
            try {
              const obj = JSON.parse(data);
              if (obj.type === "token") {
                text += obj.v;
                onToken(obj.v);
              } else if (obj.type === "citations") {
                citations = obj.v as Citation[];
              }
            } catch {
              /* ignore a malformed/partial frame */
            }
          }
        }
      return { text, citations };
    }
  } catch {
    _backendReachable = false;
    /* fall through to the non-streaming fallback */
  }

  // Fallback: single-shot answer (also the demo-mode path).
  try {
    const resp = await request<AiResponse>("/ai/chat", { method: "POST", body: payload });
    const text = resp.text ?? "";
    if (text) onToken(text);
    return { text, citations: resp.citations ?? [] };
  } catch {
    return { text: "", citations: [] };
  }
}

export interface AccountSummary {
  cash: number | null;
}

export interface PortfolioOverview {
  account: AccountOut;
  positions: Position[];
  quotes: QuoteOut[];
  cash: number | null;
}

export function getAccountSummary(accountId: string): Promise<AccountSummary> {
  return request<AccountSummary>(`/positions/${accountId}/summary`);
}

export function getPortfolioOverview(accountId: string): Promise<PortfolioOverview> {
  return request<PortfolioOverview>(`/positions/${accountId}/overview`);
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

export interface DashboardBootstrap {
  account: AccountOut | null;
  stocks: StockRow[];
  movers: Movers;
  bots: Bot[];
  signals: Signal[];
  featured_symbol: string;
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

export function getDashboardBootstrap(): Promise<DashboardBootstrap> {
  return request<DashboardBootstrap>("/dashboard/bootstrap", { cache: "no-cache" });
}
