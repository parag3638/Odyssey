"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import {
  Button,
  EmptyState,
  KpiStrip,
  Ranges,
  Skeleton,
  Tabs,
  TickerLogo,
} from "@/components/ui";
import { AnalysisTab, DividendsTab, EarningsTab, NewsTab } from "@/components/StockResearchPanels";
import { AiSummaryCard } from "@/components/AiSummaryCard";
import { BullBearPanel } from "@/components/BullBearPanel";
import { CongressContext } from "@/components/CongressContext";
import { LineChart } from "@/components/ui/LineChart";
import {
  ArrowDownRightIcon,
  ArrowUpRightIcon,
  SignalsIcon,
  SparklesIcon,
} from "@/components/icons";
import { initials, pct, splitMoney } from "@/lib/format";
import { buildKpis } from "@/lib/kpis";
import { makeSeries } from "@/lib/sample";
import {
  createBot,
  getStockHistory,
  getResearchAi,
  getResearchBootstrap,
  listAccounts,
  type Signal,
} from "@/lib/api";

const RANGES = ["1D", "1W", "1M", "3M", "YTD", "1Y", "ALL"];

/** Full single-stock view: chart, KPI strip, congressional activity, and the
 *  News/Earnings/Dividends/Analysis tab rail. Shared by /stocks/[symbol] and
 *  /research/[symbol] so both destinations render identically. */
export function StockDetailView({ symbol }: { symbol: string }) {
  const router = useRouter();

  const [range, setRange] = useState("1M");
  const [tab, setTab] = useState("news");
  const [accountId, setAccountId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [pollAi, setPollAi] = useState(false);

  const bundleQuery = useSWR(
    symbol ? ["research-bootstrap", symbol] : null,
    () => getResearchBootstrap(symbol, "1M"),
    { dedupingInterval: 60_000, revalidateOnFocus: false, keepPreviousData: true },
  );
  const bundle = bundleQuery.data;

  useEffect(() => {
    if (!bundle?.ai_pending) return;
    const id = window.setTimeout(() => setPollAi(true), 2500);
    return () => window.clearTimeout(id);
  }, [bundle?.ai_pending, symbol]);

  const aiQuery = useSWR(
    pollAi ? ["research-ai", symbol] : null,
    () => getResearchAi(symbol),
    {
      refreshInterval: (latest) => latest?.pending ? 3000 : 0,
      revalidateOnFocus: false,
    },
  );
  const historyQuery = useSWR(
    range !== "1M" ? ["stock-history", symbol, range] : null,
    () => getStockHistory(symbol, range),
    { dedupingInterval: 5 * 60_000, revalidateOnFocus: false, keepPreviousData: true },
  );

  const stock = bundle?.stock ?? null;
  const history = useMemo(
    () => range === "1M" ? bundle?.history ?? [] : historyQuery.data ?? [],
    [range, bundle?.history, historyQuery.data],
  );
  const signals = bundle?.signals ?? [];
  const news = bundle?.news ?? [];
  const earnings = bundle?.earnings ?? [];
  const analysis = bundle?.analysis ?? [];
  const dividends = bundle?.dividends ?? [];
  const aiSummary = aiQuery.data?.summary ?? bundle?.ai_summary;
  const bullBear = aiQuery.data?.bull_bear ?? bundle?.bull_bear;
  const aiPending = aiQuery.data?.pending ?? bundle?.ai_pending ?? false;

  const price = stock?.price ?? null;
  const change = stock?.change ?? null;
  const changePct = stock?.change_pct ?? null;
  const up = (changePct ?? 0) >= 0;

  const series = useMemo(() => {
    if (history.length > 1) return history.map((h) => h.price);
    return makeSeries(120, price ?? 100, up ? 8 : -8, symbol.length + 3); // illustrative fallback
  }, [history, price, up, symbol]);
  const realChart = history.length > 1;

  const resolveAccountId = useCallback(async () => {
    if (accountId) return accountId;
    const accounts = await listAccounts();
    const id = accounts[0] ? Number(accounts[0].id) : null;
    if (id) setAccountId(id);
    return id;
  }, [accountId]);

  const createTrailingBot = useCallback(async () => {
    setBusy(true);
    try {
      const id = await resolveAccountId();
      if (!id) {
        setBusy(false);
        return;
      }
      const bot = await createBot({
        name: `${symbol} trail`,
        account_id: id,
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
  }, [resolveAccountId, symbol, router]);

  const copyPolitician = useCallback(
    async (politician: string) => {
      setBusy(true);
      try {
        const id = await resolveAccountId();
        if (!id) {
          setBusy(false);
          return;
        }
        const bot = await createBot({
          name: `copy ${politician}`,
          account_id: id,
          strategy_type: "copy_trade",
          politician,
        });
        router.push(`/bots/${bot.id}`);
      } catch {
        setBusy(false);
      }
    },
    [resolveAccountId, router],
  );

  const { whole, cents } = splitMoney(price ?? 0);

  if (bundleQuery.isLoading && !stock) {
    return (
      <div className="tcard" style={{ padding: "22px" }}>
        <Skeleton w={220} h={20} />
        <Skeleton w={140} h={36} style={{ marginTop: 16 }} />
      </div>
    );
  }

  return (
    <>
      <div className="d-head">
        <TickerLogo symbol={symbol} square />
        <div>
          <div className="tk">{symbol}</div>
          <div className="nm3">
            {stock?.name || symbol}
            {stock?.sector ? ` · ${stock.sector}` : ""}
          </div>
        </div>
        <span className="sp" />
        <Button variant="ghost" sm onClick={createTrailingBot} disabled={busy}>
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

          <BullBearPanel symbol={symbol} value={bullBear} pending={aiPending} />

          <CongressionalActivity
            signals={signals}
            busy={busy}
            onCopy={copyPolitician}
            symbol={symbol}
          />
        </div>

        <div>
          <AiSummaryCard symbol={symbol} value={aiSummary} pending={aiPending} />
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
            {tab === "news" && <NewsTab items={news} loading={bundleQuery.isLoading} />}
            {tab === "earnings" && <EarningsTab items={earnings} />}
            {tab === "dividends" && <DividendsTab items={dividends} />}
            {tab === "analysis" && <AnalysisTab items={analysis} />}
          </div>
        </div>
      </div>
    </>
  );
}

/* Congressional disclosures for this symbol. Lives in the left column under the
   KPI strip so it fills the space beside the (taller) news rail. */
function CongressionalActivity({
  signals,
  busy,
  onCopy,
  symbol,
}: {
  signals: Signal[];
  busy: boolean;
  onCopy: (politician: string) => void;
  symbol: string;
}) {
  return (
    <div style={{ marginTop: 26 }}>
      <div className="sec-h">
        <h2>
          Congressional activity <span className="cnt">· {signals.length}</span>
        </h2>
      </div>
      {signals.length > 0 && <CongressContext symbol={symbol} />}
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
                      <Button sm variant="ghost" disabled={busy} onClick={() => onCopy(s.politician)}>
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
