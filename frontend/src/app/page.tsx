"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  listSignals,
  listStocks,
  type Signal,
  type StockRow,
} from "@/lib/api";
import { initials, money, signClass, signedMoney, compact } from "@/lib/format";
import { usePortfolio } from "@/lib/usePortfolio";
import { Nav } from "@/components/Nav";
import { ActiveBots } from "@/components/ActiveBots";
import { CreateBotForm } from "@/components/CreateBotForm";
import { OrderForm } from "@/components/OrderForm";
import { HoldingsRail } from "@/components/HoldingsRail";
import { StocksTable } from "@/components/StocksTable";
import { MoversWidget } from "@/components/MoversWidget";
import { PoliticianTrades } from "@/components/PoliticianTrades";
import { Card, Modal, PromoCarousel, Skeleton, Stat, StatGrid, KpiStrip } from "@/components/ui";
import { PlusIcon, SparklesIcon } from "@/components/icons";
import { StockHero } from "@/components/StockHero";
import { useStockHero } from "@/lib/useStockHero";
import { buildKpis, statPE } from "@/lib/kpis";

const PROMO = [
  { title: "Automate a trailing stop", desc: "Let a bot follow a stock up and lock in gains with a moving floor." },
  { title: "Mirror congressional trades", desc: "Copy disclosed buys and sells from Capitol Trades, sized to your rules." },
];

export default function HomePage() {
  const pf = usePortfolio();
  const [stocks, setStocks] = useState<StockRow[]>([]);
  const [signals, setSignals] = useState<Signal[]>([]);
  const [greet, setGreet] = useState("Welcome Back");
  const [selected, setSelected] = useState("");
  const [range, setRange] = useState("1M");
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

  // Initialize selected stock to the first in topCap
  useEffect(() => {
    if (!selected && topCap.length > 0) {
      setSelected(topCap[0].symbol);
    }
  }, [topCap, selected]);

  // Single-stock hero: selected row + dropdown options
  const selectedRow = topCap.find((s) => s.symbol === selected) ?? (topCap[0] || null);
  const stockOptions = useMemo(
    () => topCap.map((s) => ({ label: `${s.symbol} · ${s.name}`, value: s.symbol })),
    [topCap],
  );
  const isUp = (selectedRow?.change_pct ?? 0) >= 0;
  const hero = useStockHero(selected, range, selectedRow?.price ?? null, isUp);

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
                selected={selected}
                onSelect={setSelected}
                range={range}
                onRange={setRange}
                series={hero.series}
                dates={hero.dates}
                realChart={hero.realChart}
                loadingChart={hero.loadingChart}
              />
            </div>

            <Card pad className="reveal" style={{ marginTop: 16, ["--i" as string]: 1 }}>
              <StatGrid>
                <Stat
                  value={selectedRow ? money(selectedRow.price ?? 0) : <Skeleton w={96} h={20} />}
                  label="Price"
                  hl
                />
                <Stat
                  value={
                    selectedRow ? signedMoney(selectedRow.change ?? 0) : <Skeleton w={80} h={20} />
                  }
                  label="Day change"
                  tone={selectedRow ? signClass(selectedRow.change ?? 0) : ""}
                />
                <Stat
                  value={
                    selectedRow
                      ? selectedRow.market_cap != null
                        ? compact(selectedRow.market_cap)
                        : "—"
                      : <Skeleton w={80} h={20} />
                  }
                  label="Market cap"
                />
                <Stat
                  value={
                    hero.loadingDetail ? (
                      <Skeleton w={60} h={20} />
                    ) : (
                      statPE(hero.detail).value
                    )
                  }
                  label={statPE(hero.detail).label}
                />
              </StatGrid>
            </Card>

            <div className="reveal" style={{ marginTop: 16, ["--i" as string]: 2 }}>
              {hero.detail ? (
                <KpiStrip items={buildKpis(hero.detail)} />
              ) : (
                <Card pad>
                  <div className="faint" style={{ fontSize: 12.5 }}>
                    {hero.loadingDetail ? "Loading fundamentals…" : "Fundamentals unavailable."}
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
            <HoldingsRail holdings={pf.holdings} />
            <PromoCarousel slides={PROMO} />
            <MoversWidget gainers={moverData.gainers} losers={moverData.losers} />
            <PoliticianTrades signals={signals} />
          </aside>
        </div>

        <div className="foot">
          <b>Odyssey</b>
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
