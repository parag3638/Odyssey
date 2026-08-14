"use client";

import { useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import useSWR from "swr";
import { getDashboardBootstrap } from "@/lib/api";
import { ActiveBots } from "@/components/ActiveBots";
import { StocksTable } from "@/components/StocksTable";
import { MoversWidget } from "@/components/MoversWidget";
import { PoliticianTrades } from "@/components/PoliticianTrades";
import { Button, Card, Modal, PromoCarousel, KpiStrip } from "@/components/ui";
import { PlusIcon, SparklesIcon } from "@/components/icons";
import { StockHero } from "@/components/StockHero";
import { useStockHero } from "@/lib/useStockHero";
import { buildKpis } from "@/lib/kpis";

const CreateBotForm = dynamic(
  () => import("@/components/CreateBotForm").then((module) => module.CreateBotForm),
  { ssr: false },
);
const ConnectAccountForm = dynamic(
  () => import("@/components/ConnectAccountForm").then((module) => module.ConnectAccountForm),
  { ssr: false },
);
const OrderForm = dynamic(
  () => import("@/components/OrderForm").then((module) => module.OrderForm),
  { ssr: false },
);

const DEFAULT_FEATURED_SYMBOL = "NVDA";

const PROMO = [
  { title: "Automate a trailing stop", desc: "Let a bot follow a stock up and lock in gains with a moving floor." },
  { title: "Mirror congressional trades", desc: "Copy disclosed buys and sells from Capitol Trades, sized to your rules." },
];

export default function HomePage() {
  const dashboard = useSWR("dashboard-bootstrap", getDashboardBootstrap, {
    dedupingInterval: 30_000,
    revalidateOnFocus: false,
    keepPreviousData: true,
  });
  const [greet, setGreet] = useState("Welcome Back");
  const [selected, setSelected] = useState(DEFAULT_FEATURED_SYMBOL);
  const [range, setRange] = useState("1M");
  const [showOrder, setShowOrder] = useState(false);
  const [showBot, setShowBot] = useState(false);
  const [showConnect, setShowConnect] = useState(false);

  useEffect(() => {
    const id = requestAnimationFrame(() => {
      const h = new Date().getHours();
      setGreet(h < 12 ? "Good Morning" : h < 18 ? "Good Afternoon" : "Good Evening");
    });
    return () => cancelAnimationFrame(id);
  }, []);

  const topCap = useMemo(() => dashboard.data?.stocks ?? [], [dashboard.data?.stocks]);
  const signals = dashboard.data?.signals ?? [];
  const bots = dashboard.data?.bots ?? [];
  const moverData = dashboard.data?.movers ?? { gainers: [], losers: [] };

  // Single-stock hero: ticker-rail options + the effective selection (default to
  // the largest-cap stock until the user picks one — derived during render).
  const stockOptions = useMemo(
    () => topCap.map((s) => ({ label: `${s.symbol} · ${s.name}`, value: s.symbol })),
    [topCap],
  );
  const selectedSymbol = selected || dashboard.data?.featured_symbol || DEFAULT_FEATURED_SYMBOL;
  const selectedRow = topCap.find((s) => s.symbol === selectedSymbol) ?? null;
  const isUp = (selectedRow?.change_pct ?? 0) >= 0;
  const hero = useStockHero(selectedSymbol, range, selectedRow?.price ?? null, isUp);

  const account = dashboard.data?.account ?? null;
  return (
    <>
      <div className="wrap roomy">
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
              <Link href="/stocks" prefetch={false}>
                Browse all →
              </Link>
            </div>
            <StocksTable
              rows={topCap.slice(0, 5)}
              loading={dashboard.isLoading}
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
            <ActiveBots compact data={bots} dataLoading={dashboard.isLoading} />
            <PromoCarousel slides={PROMO} />
            <MoversWidget
              gainers={moverData.gainers}
              losers={moverData.losers}
              loading={dashboard.isLoading}
            />
            <PoliticianTrades signals={signals} loading={dashboard.isLoading} />
          </aside>
        </div>
      </div>

      {showOrder && (
        <Modal open onClose={() => setShowOrder(false)} title="New order" width={460}>
          {account ? (
            <OrderForm
              bare
              accountId={account.id}
              onPlaced={() => {
                setShowOrder(false);
              }}
            />
          ) : (
            <div>
              <div className="faint">Connect an account to place orders.</div>
              <Button
                variant="buy"
                style={{ marginTop: 12 }}
                onClick={() => {
                  setShowOrder(false);
                  setShowConnect(true);
                }}
              >
                Connect account
              </Button>
            </div>
          )}
        </Modal>
      )}

      {showBot && (
        <Modal open onClose={() => setShowBot(false)} title="New bot" width={560}>
          <CreateBotForm
            bare
            initialAccounts={account ? [account] : []}
            onNeedAccount={() => {
              setShowBot(false);
              setShowConnect(true);
            }}
          />
        </Modal>
      )}

      {showConnect && (
        <Modal open onClose={() => setShowConnect(false)} title="Connect account" width={520}>
          <ConnectAccountForm
            bare
            onConnected={() => {
              void dashboard.mutate();
              setShowConnect(false);
            }}
          />
        </Modal>
      )}
    </>
  );
}
