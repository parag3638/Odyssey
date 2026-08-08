"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { Nav } from "@/components/Nav";
import { ResearchSearch } from "@/components/ResearchSearch";
import { KpiStrip, SegmentedControl, Skeleton, TickerLogo } from "@/components/ui";
import { AnalysisTab, DividendsTab, EarningsTab, NewsTab } from "@/components/StockResearchPanels";
import { ChevronLeftIcon } from "@/components/icons";
import { pct, splitMoney } from "@/lib/format";
import { buildKpis } from "@/lib/kpis";
import {
  getStock,
  getStockAnalysis,
  getStockDividends,
  getStockEarnings,
  getStockNews,
  type DividendPoint,
  type EarningsPoint,
  type NewsArticle,
  type RecommendationPoint,
  type StockDetailData,
} from "@/lib/api";

export default function ResearchSymbolPage() {
  const params = useParams<{ symbol: string }>();
  const symbol = (params.symbol || "").toUpperCase();
  const router = useRouter();

  const [stock, setStock] = useState<StockDetailData | null>(null);
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState<"fundamentals" | "news">("fundamentals");
  const [news, setNews] = useState<NewsArticle[]>([]);
  const [newsLoading, setNewsLoading] = useState(true);
  const [earnings, setEarnings] = useState<EarningsPoint[]>([]);
  const [analysis, setAnalysis] = useState<RecommendationPoint[]>([]);
  const [dividends, setDividends] = useState<DividendPoint[]>([]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setView("fundamentals");
      getStock(symbol)
        .then((s) => !cancelled && setStock(s))
        .catch(() => !cancelled && setStock(null))
        .finally(() => !cancelled && setLoading(false));

      setNewsLoading(true);
      getStockNews(symbol)
        .then((d) => !cancelled && setNews(d))
        .catch(() => !cancelled && setNews([]))
        .finally(() => !cancelled && setNewsLoading(false));

      getStockEarnings(symbol).then((d) => !cancelled && setEarnings(d)).catch(() => {});
      getStockAnalysis(symbol).then((d) => !cancelled && setAnalysis(d)).catch(() => {});
      getStockDividends(symbol).then((d) => !cancelled && setDividends(d)).catch(() => {});
    })();

    return () => {
      cancelled = true;
    };
  }, [symbol]);

  const price = stock?.price ?? null;
  const changePct = stock?.change_pct ?? null;
  const up = (changePct ?? 0) >= 0;
  const { whole, cents } = splitMoney(price ?? 0);

  const kpis = useMemo(() => (stock ? buildKpis(stock) : []), [stock]);

  return (
    <>
      <Nav active="research" accountLabel="my-paper" accountInitials="MY" />

      <div className="wrap wide">
        <Link className="back" href="/research">
          <ChevronLeftIcon />
          Research
        </Link>

        <div className="reveal" style={{ ["--i" as string]: 0, maxWidth: 480, marginBottom: 22 }}>
          <ResearchSearch onSelect={(s) => router.push(`/research/${s}`)} />
        </div>

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
                <div className="tk">{symbol}</div>
                <div className="nm3">
                  {stock?.name || symbol}
                  {stock?.sector ? ` · ${stock.sector}` : ""}
                </div>
              </div>
            </div>

            <div className="prices" style={{ marginBottom: 22 }}>
              <div>
                <div className="lbl">At close</div>
                <div>
                  <span className="pr tnum">
                    ${whole}
                    <span className="dec">.{cents}</span>
                  </span>
                  {stock?.change != null && changePct != null && (
                    <span className={`ch ${up ? "pos" : "neg"}`}>
                      {up ? "+" : "−"}
                      {Math.abs(stock.change).toFixed(2)} ({pct(changePct)})
                    </span>
                  )}
                </div>
              </div>
              {stock?.industry && <span className="exch">{stock.industry}</span>}
            </div>

            <SegmentedControl
              ariaLabel="View"
              value={view}
              onChange={setView}
              options={[
                { value: "fundamentals", label: "Fundamentals" },
                { value: "news", label: "News" },
              ]}
            />

            {view === "fundamentals" ? (
              <div style={{ marginTop: 22 }}>
                {stock ? (
                  <KpiStrip items={kpis} />
                ) : (
                  <div className="faint" style={{ fontSize: 12.5 }}>Fundamentals unavailable.</div>
                )}

                <div className="sec-h">
                  <h2>Analyst ratings</h2>
                </div>
                <div className="tcard" style={{ padding: "16px 18px" }}>
                  <AnalysisTab items={analysis} />
                </div>

                <div className="sec-h">
                  <h2>Earnings</h2>
                </div>
                <div className="tcard" style={{ padding: "16px 18px" }}>
                  <EarningsTab items={earnings} />
                </div>

                <div className="sec-h">
                  <h2>Dividends</h2>
                </div>
                <div className="tcard" style={{ padding: "16px 18px" }}>
                  <DividendsTab items={dividends} />
                </div>
              </div>
            ) : (
              <div className="tcard" style={{ marginTop: 22, padding: "16px 18px" }}>
                <NewsTab items={news} loading={newsLoading} />
              </div>
            )}
          </>
        )}
      </div>
    </>
  );
}
