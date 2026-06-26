"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { Nav } from "@/components/Nav";
import {
  Button,
  EmptyState,
  KpiStrip,
  Pill,
  Ranges,
  Skeleton,
  Tabs,
  TickerLogo,
} from "@/components/ui";
import { LineChart } from "@/components/ui/LineChart";
import {
  ArrowDownRightIcon,
  ArrowUpRightIcon,
  ChevronLeftIcon,
  SignalsIcon,
  SparklesIcon,
} from "@/components/icons";
import { initials, pct, splitMoney } from "@/lib/format";
import { buildKpis } from "@/lib/kpis";
import { makeSeries } from "@/lib/sample";
import {
  createBot,
  getStock,
  getStockAnalysis,
  getStockDividends,
  getStockEarnings,
  getStockHistory,
  getStockNews,
  getStockSignals,
  listAccounts,
  type DividendPoint,
  type EarningsPoint,
  type HistoryPoint,
  type NewsArticle,
  type RecommendationPoint,
  type Signal,
  type StockDetailData,
} from "@/lib/api";

const RANGES = ["1D", "1W", "1M", "3M", "YTD", "1Y", "ALL"];

export default function StockPage() {
  const params = useParams<{ symbol: string }>();
  const symbol = (params.symbol || "").toUpperCase();
  const router = useRouter();

  const [stock, setStock] = useState<StockDetailData | null>(null);
  const [history, setHistory] = useState<HistoryPoint[]>([]);
  const [range, setRange] = useState("1M");
  const [tab, setTab] = useState("news");
  const [news, setNews] = useState<NewsArticle[]>([]);
  const [earnings, setEarnings] = useState<EarningsPoint[]>([]);
  const [analysis, setAnalysis] = useState<RecommendationPoint[]>([]);
  const [dividends, setDividends] = useState<DividendPoint[]>([]);
  const [signals, setSignals] = useState<Signal[]>([]);
  const [accountId, setAccountId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const s = await getStock(symbol);
        if (!cancelled) setStock(s);
      } catch {
        /* ignore */
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    getStockNews(symbol).then((d) => !cancelled && setNews(d)).catch(() => {});
    getStockEarnings(symbol).then((d) => !cancelled && setEarnings(d)).catch(() => {});
    getStockAnalysis(symbol).then((d) => !cancelled && setAnalysis(d)).catch(() => {});
    getStockDividends(symbol).then((d) => !cancelled && setDividends(d)).catch(() => {});
    getStockSignals(symbol).then((d) => !cancelled && setSignals(d)).catch(() => {});
    listAccounts().then((a) => !cancelled && a[0] && setAccountId(Number(a[0].id))).catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  useEffect(() => {
    let cancelled = false;
    getStockHistory(symbol, range).then((h) => !cancelled && setHistory(h)).catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [symbol, range]);

  const price = stock?.price ?? null;
  const change = stock?.change ?? null;
  const changePct = stock?.change_pct ?? null;
  const up = (changePct ?? 0) >= 0;

  const series = useMemo(() => {
    if (history.length > 1) return history.map((h) => h.price);
    return makeSeries(120, price ?? 100, up ? 8 : -8, symbol.length + 3); // illustrative fallback
  }, [history, price, up, symbol]);
  const realChart = history.length > 1;

  const createTrailingBot = useCallback(async () => {
    if (!accountId) return;
    setBusy(true);
    try {
      const bot = await createBot({
        name: `${symbol} trail`,
        account_id: accountId,
        strategy_type: "trailing_stop",
        symbol,
        initial_shares: 10,
        stop_pct: 0.1,
        trail_pct: 0.05,
      });
      router.push(`/bots/${bot.id}`);
    } catch {
      setBusy(false);
    }
  }, [accountId, symbol, router]);

  const copyPolitician = useCallback(
    async (politician: string) => {
      if (!accountId) return;
      setBusy(true);
      try {
        const bot = await createBot({
          name: `copy ${politician}`,
          account_id: accountId,
          strategy_type: "copy_trade",
          politician,
        });
        router.push(`/bots/${bot.id}`);
      } catch {
        setBusy(false);
      }
    },
    [accountId, router],
  );

  const { whole, cents } = splitMoney(price ?? 0);

  return (
    <>
      <Nav active="stocks" accountLabel="my-paper" accountInitials="MY" />

      <div className="wrap wide">
        <Link className="back" href="/stocks">
          <ChevronLeftIcon />
          Stocks
        </Link>

        {loading && !stock ? (
          <div className="tcard" style={{ padding: "22px" }}>
            <Skeleton w={220} h={20} />
            <Skeleton w={140} h={36} style={{ marginTop: 16 }} />
          </div>
        ) : (
          <>
            <div className="d-head">
              <TickerLogo symbol={symbol} square />
              <div>
                <div className="tk">
                  {symbol}
                </div>
                <div className="nm3">
                  {stock?.name || symbol}
                  {stock?.sector ? ` · ${stock.sector}` : ""}
                </div>
              </div>
              <span className="sp" />
              <Button variant="ghost" sm onClick={createTrailingBot} disabled={busy || !accountId}>
                <SparklesIcon />
                Create bot
              </Button>
            </div>

            <div className="prices" style={{ marginBottom: 8 }}>
              <div>
                <div className="lbl">At close</div>
                <div>
                  <span className="pr tnum">
                    ${whole}
                    <span className="dec">.{cents}</span>
                  </span>
                  {change != null && changePct != null && (
                    <span className={`ch ${up ? "pos" : "neg"}`}>
                      {up ? "+" : "−"}
                      {Math.abs(change).toFixed(2)} ({pct(changePct)})
                    </span>
                  )}
                </div>
              </div>
              {stock?.industry && <span className="exch">{stock.industry}</span>}
            </div>

            <div className="d-grid">
              <div>
                {!realChart && (
                  <div className="faint" style={{ fontSize: 12, marginBottom: 8 }}>
                    Price history unavailable — showing an illustrative line.
                  </div>
                )}
                <LineChart
                  data={series}
                  height={300}
                  tone={up ? "gain" : "loss"}
                  area
                  volume
                  crosshair
                  grid
                  hover="tooltip"
                  dates={
                    realChart
                      ? history.map((h) => new Date(h.t).toLocaleString("en-US", { month: "short", day: "numeric" }))
                      : undefined
                  }
                />
                <div style={{ marginTop: 14 }}>
                  <Ranges options={RANGES} value={range} onChange={setRange} />
                </div>
                <div style={{ marginTop: 22 }}>
                  {stock && <KpiStrip items={buildKpis(stock)} />}
                </div>

                <CongressionalActivity
                  signals={signals}
                  busy={busy}
                  accountId={accountId}
                  onCopy={copyPolitician}
                />
              </div>

              <div>
                <div className="rtabs">
                  <Tabs
                    options={[
                      { label: "News", value: "news" },
                      { label: "Earnings", value: "earnings" },
                      { label: "Dividends", value: "dividends" },
                      { label: "Analysis", value: "analysis" },
                    ]}
                    value={tab}
                    onChange={setTab}
                  />
                </div>
                <div className="newssum">
                  {tab === "news" && <NewsTab items={news} />}
                  {tab === "earnings" && <EarningsTab items={earnings} />}
                  {tab === "dividends" && <DividendsTab items={dividends} />}
                  {tab === "analysis" && <AnalysisTab items={analysis} />}
                </div>
              </div>
            </div>

            <div className="foot">
              <b>Odyssey</b>
            </div>
          </>
        )}
      </div>
    </>
  );
}

/* Congressional disclosures for this symbol. Lives in the left column under the
   KPI strip so it fills the space beside the (taller) news rail. */
function CongressionalActivity({
  signals,
  busy,
  accountId,
  onCopy,
}: {
  signals: Signal[];
  busy: boolean;
  accountId: number | null;
  onCopy: (politician: string) => void;
}) {
  return (
    <div style={{ marginTop: 26 }}>
      <div className="sec-h">
        <h2>
          Congressional activity <span className="cnt">· {signals.length}</span>
        </h2>
      </div>
      <div className="tcard">
        {signals.length === 0 ? (
          <EmptyState
            icon={<SignalsIcon />}
            title="No disclosed trades for this stock"
            desc="Congressional buys/sells of this symbol will appear here."
          />
        ) : (
          <table>
            <thead>
              <tr>
                <th className="l">Politician</th>
                <th className="l">Action</th>
                <th>Est. size</th>
                <th>Tx date</th>
                <th className="l">Copy</th>
              </tr>
            </thead>
            <tbody>
              {signals.map((s) => {
                const buy = s.tx_type.toLowerCase() !== "sell";
                return (
                  <tr key={s.id}>
                    <td className="l">
                      <div className="sym">
                        <span className="lg" style={{ background: "var(--card-3)", color: "var(--text-2)" }}>
                          {initials(s.politician)}
                        </span>
                        <span className="tk">{s.politician}</span>
                      </div>
                    </td>
                    <td className="l">
                      <span className={`act ${buy ? "pos" : "neg"}`}>
                        {buy ? "BUY" : "SELL"}
                        {buy ? <ArrowUpRightIcon /> : <ArrowDownRightIcon />}
                      </span>
                    </td>
                    <td className="tnum">{s.amount_range || "—"}</td>
                    <td className="tnum">{s.tx_date}</td>
                    <td className="l">
                      <Button sm variant="ghost" disabled={busy || !accountId} onClick={() => onCopy(s.politician)}>
                        Copy {s.politician.split(" ").slice(-1)[0]}
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

/* ---------------- tab panels ---------------- */
function NewsTab({ items }: { items: NewsArticle[] }) {
  if (items.length === 0)
    return <div className="faint" style={{ fontSize: 13 }}>No recent news.</div>;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {items.slice(0, 8).map((a, i) => (
        <a key={i} href={a.url} target="_blank" rel="noopener noreferrer" style={{ display: "block" }}>
          <div style={{ fontWeight: 600, fontSize: 13.5, lineHeight: 1.4 }}>{a.headline}</div>
          <div className="faint" style={{ fontSize: 11.5, marginTop: 4 }}>
            {a.source}
            {a.datetime ? ` · ${new Date(a.datetime).toLocaleDateString()}` : ""}
          </div>
        </a>
      ))}
    </div>
  );
}

function EarningsTab({ items }: { items: EarningsPoint[] }) {
  if (items.length === 0)
    return <div className="faint" style={{ fontSize: 13 }}>No earnings data (add a Finnhub key).</div>;
  return (
    <div className="botstat">
      {items.slice(0, 6).map((e, i) => {
        const beat = e.actual != null && e.estimate != null && e.actual >= e.estimate;
        return (
          <div className="line" key={i}>
            <span className="k">{e.period}</span>
            <span className="v">
              <span className="tnum">{e.actual != null ? `$${e.actual.toFixed(2)}` : "—"}</span>{" "}
              {e.actual != null && e.estimate != null && (
                <Pill tone={beat ? "g" : "r"}>{beat ? "Beat" : "Miss"}</Pill>
              )}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function DividendsTab({ items }: { items: DividendPoint[] }) {
  if (items.length === 0)
    return <div className="faint" style={{ fontSize: 13 }}>No recent dividends.</div>;
  return (
    <div className="botstat">
      {items.slice(0, 8).map((d, i) => (
        <div className="line" key={i}>
          <span className="k">{d.ex_date}</span>
          <span className="v tnum">${d.amount.toFixed(2)}</span>
        </div>
      ))}
    </div>
  );
}

function AnalysisTab({ items }: { items: RecommendationPoint[] }) {
  const latest = items[0];
  if (!latest)
    return <div className="faint" style={{ fontSize: 13 }}>No analyst data (add a Finnhub key).</div>;
  const rows = [
    { k: "Strong buy", v: latest.strongBuy ?? 0, tone: "g" as const },
    { k: "Buy", v: latest.buy ?? 0, tone: "g" as const },
    { k: "Hold", v: latest.hold ?? 0, tone: "n" as const },
    { k: "Sell", v: latest.sell ?? 0, tone: "r" as const },
    { k: "Strong sell", v: latest.strongSell ?? 0, tone: "r" as const },
  ];
  const total = rows.reduce((s, r) => s + r.v, 0) || 1;
  return (
    <div>
      <div className="faint" style={{ fontSize: 11.5, marginBottom: 12 }}>
        Analyst ratings · {latest.period}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {rows.map((r) => (
          <div key={r.k} style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 12.5 }}>
            <span className="muted" style={{ width: 78 }}>{r.k}</span>
            <span className="allocbar" style={{ flex: 1, height: 8 }}>
              <span
                style={{
                  flexGrow: r.v / total,
                  background: r.tone === "g" ? "var(--gain)" : r.tone === "r" ? "var(--loss)" : "var(--text-3)",
                }}
              />
              <span style={{ flexGrow: 1 - r.v / total, background: "var(--card-3)" }} />
            </span>
            <span className="tnum" style={{ width: 22, textAlign: "right" }}>{r.v}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
