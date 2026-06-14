"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  listSignals,
  listStocks,
  type Signal,
  type StockRow,
} from "@/lib/api";
import { initials, logoColor, money, signClass, signedMoney } from "@/lib/format";
import { makeSeries } from "@/lib/sample";
import { usePortfolio } from "@/lib/usePortfolio";
import { useWatchlist } from "@/lib/useWatchlist";
import type { AllocationSlice } from "@/lib/types";
import { Nav } from "@/components/Nav";
import { PortfolioHero, HERO_RANGES } from "@/components/PortfolioHero";
import { ActiveBots } from "@/components/ActiveBots";
import { CreateBotForm } from "@/components/CreateBotForm";
import { OrderForm } from "@/components/OrderForm";
import { HoldingsRail } from "@/components/HoldingsRail";
import { StocksTable } from "@/components/StocksTable";
import { MoversWidget } from "@/components/MoversWidget";
import { PoliticianTrades } from "@/components/PoliticianTrades";
import { AssetBreakdown, Card, Modal, PromoCarousel, Skeleton, Stat, StatGrid } from "@/components/ui";
import { PlusIcon, SparklesIcon } from "@/components/icons";

const PROMO = [
  { title: "Automate a trailing stop", desc: "Let a bot follow a stock up and lock in gains with a moving floor." },
  { title: "Mirror congressional trades", desc: "Copy disclosed buys and sells from Capitol Trades, sized to your rules." },
];

export default function HomePage() {
  const pf = usePortfolio();
  const wl = useWatchlist();
  const [stocks, setStocks] = useState<StockRow[]>([]);
  const [signals, setSignals] = useState<Signal[]>([]);
  const [greet, setGreet] = useState("Welcome Back");
  const [range, setRange] = useState("1D");
  const [masked, setMasked] = useState(false);
  const [showOrder, setShowOrder] = useState(false);
  const [showBot, setShowBot] = useState(false);

  useEffect(() => {
    listStocks({ limit: 200 }).then(setStocks).catch(() => setStocks([]));
    listSignals().then(setSignals).catch(() => setSignals([]));
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

  const breakdown: AllocationSlice[] = useMemo(() => {
    const hs = pf.holdings
      .map((h) => ({ label: h.symbol, value: h.qty * (h.price ?? h.avgCost), color: logoColor(h.symbol) }))
      .sort((a, b) => b.value - a.value);
    const slices = hs.slice(0, 6);
    const rest = hs.slice(6);
    if (rest.length) slices.push({ label: "Other", value: rest.reduce((s, x) => s + x.value, 0), color: "#3a3a42" });
    if (pf.cash != null && pf.cash > 0) slices.push({ label: "Cash", value: pf.cash, color: "#6a6a72" });
    return slices;
  }, [pf.holdings, pf.cash]);

  const seed = useMemo(() => {
    let h = 7;
    for (const p of pf.positions) for (const c of p.symbol) h = (h * 31 + c.charCodeAt(0)) >>> 0;
    return h % 97;
  }, [pf.positions]);
  const series = useMemo(
    () => makeSeries(150, 120, pf.todayPct >= 0 ? 10 : -10, seed + HERO_RANGES.indexOf(range) + 1),
    [seed, range, pf.todayPct],
  );

  const accountLabel = pf.account?.label ?? "there";

  return (
    <>
      <Nav active="overview" accountLabel={accountLabel} accountInitials={initials(accountLabel)} />

      <div className="wrap wide">
        <div className="greet reveal" style={{ ["--i" as string]: 0 }}>
          {greet}, Captain
        </div>

        <div className="ov-grid">
          {/* main: chart, portfolio widgets, then the narrowed stocks table */}
          <div>
            <div className="reveal" style={{ ["--i" as string]: 0 }}>
              <PortfolioHero
                balance={pf.balance}
                masked={masked}
                onToggleMask={() => setMasked((m) => !m)}
                todayAmount={pf.todayAmount}
                todayPct={pf.todayPct}
                range={range}
                onRange={setRange}
                series={series}
                hasData={pf.hasData}
                loading={pf.loading}
              />
            </div>

            <Card pad className="reveal" style={{ marginTop: 16, ["--i" as string]: 1 }}>
              <StatGrid>
                <Stat
                  value={pf.loading ? <Skeleton w={96} h={20} /> : pf.hasData ? money(pf.balance) : "—"}
                  label="Portfolio value"
                  hl
                />
                <Stat
                  value={pf.loading ? <Skeleton w={80} h={20} /> : signedMoney(pf.todayAmount)}
                  label="Today's return"
                  tone={pf.loading ? "" : signClass(pf.todayAmount)}
                />
                <Stat
                  value={pf.loading ? <Skeleton w={80} h={20} /> : signedMoney(pf.allTime.amount)}
                  label="All-time return"
                  tone={pf.loading ? "" : signClass(pf.allTime.amount)}
                />
                <Stat
                  value={pf.loading ? <Skeleton w={96} h={20} /> : pf.cash != null ? money(pf.cash) : "—"}
                  label="Cash available"
                />
              </StatGrid>
            </Card>

            <div className="reveal" style={{ marginTop: 16, ["--i" as string]: 2 }}>
              {breakdown.length > 0 ? (
                <AssetBreakdown title="Asset breakdown" slices={breakdown} />
              ) : (
                <Card pad>
                  <div className="bk-title" style={{ marginBottom: 0 }}>Asset breakdown</div>
                  <div className="faint" style={{ fontSize: 12.5, marginTop: 8 }}>
                    Your allocation appears once you hold positions.
                  </div>
                </Card>
              )}
            </div>

            <div className="sec-h" style={{ margin: "28px 0 13px" }}>
              <h2>Stocks</h2>
              <Link href="/stocks">Browse all →</Link>
            </div>
            <StocksTable
              rows={topCap}
              loading={stocks.length === 0}
              isStarred={wl.isStarred}
              onToggleStar={wl.toggle}
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
            <HoldingsRail holdings={pf.holdings} watchlist={wl.rows} />
            <PromoCarousel slides={PROMO} />
            <MoversWidget gainers={moverData.gainers} losers={moverData.losers} />
            <PoliticianTrades signals={signals} />
          </aside>
        </div>

        <div className="foot">
          <b>Odyssey</b> · research terminal · paper trading · prices live · fundamentals via Finnhub
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
