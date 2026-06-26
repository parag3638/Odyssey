"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  listSignals,
  listStocks,
  type Signal,
  type StockRow,
} from "@/lib/api";
import { initials } from "@/lib/format";
import { usePortfolio } from "@/lib/usePortfolio";
import { Nav } from "@/components/Nav";
import { ActiveBots } from "@/components/ActiveBots";
import { CreateBotForm } from "@/components/CreateBotForm";
import { OrderForm } from "@/components/OrderForm";
import { StocksTable } from "@/components/StocksTable";
import { MoversWidget } from "@/components/MoversWidget";
import { PoliticianTrades } from "@/components/PoliticianTrades";
import { Card, Modal, PromoCarousel, KpiStrip } from "@/components/ui";
import { PlusIcon, SparklesIcon } from "@/components/icons";
import { StockHero } from "@/components/StockHero";
import { useStockHero } from "@/lib/useStockHero";
import { buildKpis } from "@/lib/kpis";

const PROMO = [
  { title: "Automate a trailing stop", desc: "Let a bot follow a stock up and lock in gains with a moving floor." },
  { title: "Mirror congressional trades", desc: "Copy disclosed buys and sells from Capitol Trades, sized to your rules." },
];

export default function HomePage() {
  const pf = usePortfolio();
  const [stocks, setStocks] = useState<StockRow[]>([]);
  const [signals, setSignals] = useState<Signal[]>([]);
  const [signalsReady, setSignalsReady] = useState(false);
  const [greet, setGreet] = useState("Welcome Back");
  const [selected, setSelected] = useState("");
  const [range, setRange] = useState("1M");
  const [showOrder, setShowOrder] = useState(false);
  const [showBot, setShowBot] = useState(false);

  useEffect(() => {
    listStocks({ limit: 200 }).then(setStocks).catch(() => setStocks([]));
    listSignals().then(setSignals).catch(() => setSignals([])).finally(() => setSignalsReady(true));
  }, []);

  useEffect(() => {
    const id = requestAnimationFrame(() => {
      const h = new Date().getHours();
      setGreet(h < 12 ? "Good Morning" : h < 18 ? "Good Afternoon" : "Good Evening");
    });
    return () => cancelAnimationFrame(id);
  }, []);

  const topCap = useMemo(
    () => [...stocks].sort((a, b) => (b.market_cap ?? 0) - (a.market_cap ?? 0)).slice(0, 8),
    [stocks],
  );
  const ranked = useMemo(
    () =>
      stocks
        .filter((s) => s.change_pct != null)
        .sort((a, b) => (b.change_pct ?? 0) - (a.change_pct ?? 0)),
    [stocks],
  );
  const moverData = useMemo(
    () => ({
      gainers: ranked.slice(0, 4).map((s) => ({ symbol: s.symbol, name: s.name, change_pct: s.change_pct })),
      losers: ranked.slice(-4).reverse().map((s) => ({ symbol: s.symbol, name: s.name, change_pct: s.change_pct })),
    }),
    [ranked],
  );

  // Single-stock hero: ticker-rail options + the effective selection (default to
  // the largest-cap stock until the user picks one — derived during render).
  const stockOptions = useMemo(
    () => topCap.map((s) => ({ label: `${s.symbol} · ${s.name}`, value: s.symbol })),
    [topCap],
  );
  const selectedSymbol = selected || topCap[0]?.symbol || "";
  const selectedRow = topCap.find((s) => s.symbol === selectedSymbol) ?? null;
  const isUp = (selectedRow?.change_pct ?? 0) >= 0;
  const hero = useStockHero(selectedSymbol, range, selectedRow?.price ?? null, isUp);

  const accountLabel = pf.account?.label ?? "there";

  return (
    <>
      <Nav active="overview" accountLabel={accountLabel} accountInitials={initials(accountLabel)} />

      <div className="wrap wide">
        <div className="greet reveal" style={{ ["--i" as string]: 0 }}>
          {greet}, Captain
        </div>

        <div className="ov-grid">
          {/* main: stock chart (from dropdown), reactive stats, then the narrowed stocks table */}
          <div>
            <div className="reveal" style={{ ["--i" as string]: 0 }}>
              <StockHero
                row={selectedRow}
                options={stockOptions}
                selected={selectedSymbol}
                onSelect={setSelected}
                range={range}
                onRange={setRange}
                series={hero.series}
                dates={hero.dates}
                realChart={hero.realChart}
                loadingChart={hero.loadingChart}
              />
            </div>

            <div className="reveal" style={{ marginTop: 16, ["--i" as string]: 1 }}>
              {hero.detail ? (
                <KpiStrip items={buildKpis(hero.detail)} />
              ) : !selectedRow || hero.loadingDetail ? (
                <KpiStrip loading />
              ) : (
                <Card pad>
                  <div className="faint" style={{ fontSize: 12.5 }}>Fundamentals unavailable.</div>
                </Card>
              )}
            </div>

            <div className="sec-h" style={{ margin: "28px 0 13px" }}>
              <h2>Stocks</h2>
              <Link href="/stocks">Browse all →</Link>
            </div>
            <StocksTable
              rows={topCap.slice(0, 5)}
              loading={stocks.length === 0}
              empty="Run the ticker seed to populate stocks."
              initialSort={{ key: "market_cap", dir: "desc" }}
              minimal
            />
          </div>

          {/* right rail: paper-trade widgets, then market intel (continuous) */}
          <aside className="rail reveal" style={{ ["--i" as string]: 1 }}>
            <div className="quickrow">
              <button type="button" className="quickbtn" onClick={() => setShowOrder(true)}>
                <PlusIcon />
                <span className="ql">New order</span>
              </button>
              <button type="button" className="quickbtn" onClick={() => setShowBot(true)}>
                <SparklesIcon />
                <span className="ql">New bot</span>
              </button>
            </div>
            <ActiveBots compact />
            <PromoCarousel slides={PROMO} />
            <MoversWidget gainers={moverData.gainers} losers={moverData.losers} loading={stocks.length === 0} />
            <PoliticianTrades signals={signals} loading={!signalsReady} />
          </aside>
        </div>
      </div>

      <Modal open={showOrder} onClose={() => setShowOrder(false)} title="New order" width={460}>
        {pf.account ? (
          <OrderForm
            bare
            accountId={pf.account.id}
            onPlaced={() => {
              pf.refresh();
              setShowOrder(false);
            }}
          />
        ) : (
          <div className="faint">Connect an account to place orders.</div>
        )}
      </Modal>

      <Modal open={showBot} onClose={() => setShowBot(false)} title="New bot" width={560}>
        <CreateBotForm bare />
      </Modal>
    </>
  );
}
