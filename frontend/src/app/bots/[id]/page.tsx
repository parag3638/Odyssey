"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { Nav } from "@/components/Nav";
import { BotStatus } from "@/components/BotStatus";
import { Badge, KpiStrip, Ranges, TickerLogo } from "@/components/ui";
import { LineChart } from "@/components/ui/LineChart";
import { ChevronLeftIcon } from "@/components/icons";
import { logoColor, money, pct, qtyFmt } from "@/lib/format";
import { makeSeries } from "@/lib/sample";
import type { KpiItem } from "@/lib/types";
import { getBot, type BotDetail } from "@/lib/api";

const RANGES = ["1D", "1W", "1M", "3M", "YTD", "All"];

export default function BotDetailPage() {
  const params = useParams<{ id: string }>();
  const id = Number(params.id);
  const validId = Number.isFinite(id);

  const [bot, setBot] = useState<BotDetail | null>(null);
  const [fetching, setFetching] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [range, setRange] = useState("1M");
  const loading = validId && fetching;
  const error = validId ? fetchError : "Invalid bot id.";

  useEffect(() => {
    if (!validId) return;
    let cancelled = false;
    (async () => {
      setFetching(true);
      setFetchError(null);
      try {
        const data = await getBot(id);
        if (!cancelled) setBot(data);
      } catch (e) {
        if (!cancelled)
          setFetchError(e instanceof Error ? e.message : "Could not load bot.");
      } finally {
        if (!cancelled) setFetching(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id, validId]);

  const symbol =
    (bot?.config?.symbol as string | undefined) ?? bot?.position?.symbol ?? "—";
  const active = bot?.status === "active";

  const series = useMemo(
    () => makeSeries(150, 100, active ? 8 : -4, (validId ? id : 1) + RANGES.indexOf(range) + 5),
    [active, id, range, validId],
  );

  const kpis: KpiItem[] = useMemo(() => {
    if (!bot) return [];
    const c = bot.config as Record<string, unknown>;
    const cadence =
      bot.schedule_cadence_sec % 60 === 0
        ? `${bot.schedule_cadence_sec / 60}m`
        : `${bot.schedule_cadence_sec}s`;
    if (bot.strategy_type === "copy_trade") {
      return [
        { k: "Strategy", v: "Copy-trade" },
        { k: "Politician", v: String(c.politician ?? "auto") },
        { k: "Per-trade", v: typeof c.per_trade_notional === "number" ? money(c.per_trade_notional) : "—" },
        { k: "Position", v: bot.position ? `${qtyFmt(bot.position.qty)} sh` : "—" },
        { k: "Cadence", v: cadence },
      ];
    }
    return [
      { k: "Strategy", v: "Trailing-stop" },
      { k: "Symbol", v: symbol },
      { k: "Shares", v: typeof c.initial_shares === "number" ? qtyFmt(c.initial_shares) : "—" },
      { k: "Stop", v: typeof c.stop_pct === "number" ? pct(c.stop_pct * 100, false) : "—" },
      { k: "Trail", v: typeof c.trail_pct === "number" ? pct(c.trail_pct * 100, false) : "—" },
      { k: "Cadence", v: cadence },
    ];
  }, [bot, symbol]);

  return (
    <>
      <Nav accountLabel="Trading Claude" accountInitials="TC" />

      <div className="wrap wide">
        <Link className="back" href="/">
          <ChevronLeftIcon />
          Overview
        </Link>

        {loading && (
          <div className="tcard" style={{ padding: "20px 22px" }}>
            <div className="faint">Loading bot…</div>
          </div>
        )}

        {!loading && error && (
          <div className="tcard" style={{ padding: "20px 22px" }}>
            <div className="neg">Could not load bot — {error}</div>
          </div>
        )}

        {!loading && !error && bot && (
          <>
            <div className="d-head">
              <TickerLogo symbol={symbol} color={logoColor(symbol)} square />
              <div>
                <div className="tk">
                  {symbol}
                  <Badge tone={active ? "g" : "n"}>{bot.status}</Badge>
                </div>
                <div className="nm3">{bot.name}</div>
              </div>
              <span className="sp" />
            </div>

            <div className="d-grid">
              <div>
                <div style={{ marginBottom: 14 }} className="faint">
                  Position performance <span style={{ opacity: 0.7 }}>· illustrative</span>
                </div>
                <LineChart
                  data={series}
                  height={280}
                  tone={active ? "gain" : "loss"}
                  area
                  crosshair
                  hover="date"
                  grid
                  dates={series.map((_, i) => `Day ${i + 1}`)}
                />
                <div style={{ marginTop: 14 }}>
                  <Ranges options={RANGES} value={range} onChange={setRange} />
                </div>
                <div style={{ marginTop: 22 }}>
                  <KpiStrip items={kpis} />
                </div>
              </div>

              <BotStatus bot={bot} />
            </div>

            <div className="foot">
              <b>Odyssey</b> · paper trading · performance chart is illustrative
            </div>
          </>
        )}
      </div>
    </>
  );
}
