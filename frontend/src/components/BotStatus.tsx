"use client";

import { useState, useTransition } from "react";
import { getBot, runBot, type BotDetail } from "@/lib/api";
import { money, pct } from "@/lib/format";
import { Badge } from "@/components/ui";
import { BotPulseIcon, SignalsIcon, SparklesIcon } from "./icons";

function cadenceLabel(seconds: number): string {
  if (seconds % 3600 === 0) return `checks every ${seconds / 3600}h`;
  if (seconds % 60 === 0) return `checks every ${seconds / 60}m`;
  return `checks every ${seconds}s`;
}

function timelineText(event: string, detail: Record<string, unknown>) {
  const symbol = typeof detail.symbol === "string" ? detail.symbol : "";
  const price = typeof detail.price === "number" ? detail.price : null;
  const floor = typeof detail.stop_floor === "number" ? detail.stop_floor : null;
  switch (event) {
    case "tick":
      return (
        <>
          <b>Tick</b> {symbol}
          {price !== null ? ` @ ${money(price)}` : ""}
          {floor !== null ? ` · floor ${money(floor)}` : ""}
        </>
      );
    case "tick_action_skipped":
      return (
        <>
          <b>Skipped action</b>{" "}
          {typeof detail.reason === "string" ? detail.reason : symbol}
        </>
      );
    case "order_filled":
      return (
        <>
          <b>Filled</b> {symbol}
          {price !== null ? ` @ ${money(price)}` : ""}
        </>
      );
    default:
      return (
        <>
          <b>{event}</b> {symbol}
        </>
      );
  }
}

export function BotStatus({ bot: initial }: { bot: BotDetail }) {
  const [bot, setBot] = useState<BotDetail>(initial);
  const [isRunning, startRun] = useTransition();
  const [runError, setRunError] = useState<string | null>(null);

  const isCopy = bot.strategy_type === "copy_trade";
  const config = bot.config as {
    symbol?: string;
    stop_pct?: number;
    trail_pct?: number;
    ladder?: { drop_pct: number; add_shares: number }[];
    politician?: string;
    per_trade_notional?: number;
    follow_buys?: boolean;
    follow_sells?: boolean;
  };
  const pos = bot.position;
  const active = bot.status === "active";

  const trailPct = typeof config.trail_pct === "number" ? config.trail_pct : null;
  const stopFloor = pos?.stop_floor ?? null;
  const avgEntry = pos?.avg_entry_price ?? null;
  let downside: number | null = null;
  if (stopFloor !== null && avgEntry !== null && avgEntry > 0) {
    downside = stopFloor / avgEntry - 1;
  }
  const triggered = pos?.triggered_rungs ?? [];
  const nextRung = (config.ladder ?? []).find((r) => !triggered.includes(r.drop_pct));

  const follows = [config.follow_buys !== false ? "buys" : null, config.follow_sells !== false ? "sells" : null]
    .filter(Boolean)
    .join(" & ");

  function refresh() {
    startRun(async () => {
      setRunError(null);
      try {
        await runBot(bot.id);
        setBot(await getBot(bot.id));
      } catch (e) {
        setRunError(e instanceof Error ? e.message : "Run failed.");
      }
    });
  }

  return (
    <div className="rpanel">
      <div className="ph">
        <span className="ic">{isCopy ? <SignalsIcon /> : <BotPulseIcon />}</span>
        <span className="tt">{isCopy ? "Copy-trade" : "Trailing-stop"}</span>
        <span className="when">{cadenceLabel(bot.schedule_cadence_sec)}</span>
      </div>

      <div className="botstat">
        <div className="line">
          <span className="k">Status</span>
          <span className="v">
            <Badge tone={active ? "g" : "n"}>{bot.status}</Badge>
          </span>
        </div>

        {isCopy ? (
          <>
            <div className="line">
              <span className="k">Politician</span>
              <span className="v">{config.politician || "auto"}</span>
            </div>
            <div className="line">
              <span className="k">Per-trade size</span>
              <span className="v tnum">
                {typeof config.per_trade_notional === "number"
                  ? money(config.per_trade_notional)
                  : "—"}
              </span>
            </div>
            <div className="line">
              <span className="k">Mirrors</span>
              <span className="v">{follows || "—"}</span>
            </div>
            <div className="line">
              <span className="k">Position</span>
              <span className="v tnum">
                {pos ? `${pos.qty} sh ${pos.symbol}` : "—"}
              </span>
            </div>
          </>
        ) : (
          <>
            <div className="line">
              <span className="k">Stop floor</span>
              <span className="v tnum">{stopFloor !== null ? money(stopFloor) : "—"}</span>
            </div>
            <div className="line">
              <span className="k">Trail distance</span>
              <span className="v tnum">{trailPct !== null ? pct(trailPct * 100, false) : "—"}</span>
            </div>
            <div className="line">
              <span className="k">Position</span>
              <span className="v tnum">
                {pos ? `${pos.qty} sh ${config.symbol ?? pos.symbol}` : "—"}
              </span>
            </div>
            <div className="line">
              <span className="k">Downside to floor</span>
              <span className={`v tnum ${downside !== null && downside < 0 ? "neg" : ""}`}>
                {downside !== null ? pct(downside * 100) : "—"}
              </span>
            </div>
            <div className="line">
              <span className="k">Next ladder buy</span>
              <span className="v tnum">
                {nextRung
                  ? `−${Math.round(nextRung.drop_pct * 100)}% · +${nextRung.add_shares} sh`
                  : "none"}
              </span>
            </div>
          </>
        )}
      </div>

      <div className="timeline">
        {bot.recent_activity.length === 0 ? (
          <div className="empty">No activity yet — run a tick to begin.</div>
        ) : (
          bot.recent_activity.map((a, i) => (
            <div key={i} className={`ti${i === 0 ? " now" : ""}`}>
              <span className="dot" />
              <span className="tx">{timelineText(a.event, a.detail)}</span>
            </div>
          ))
        )}
      </div>

      <button type="button" className="explain" onClick={refresh} disabled={isRunning}>
        <SparklesIcon />
        {isRunning ? "Running tick…" : "Run tick now"}
      </button>

      {runError && (
        <div className="order-status neg" role="status" style={{ marginTop: 10 }}>
          {runError}
        </div>
      )}
    </div>
  );
}
