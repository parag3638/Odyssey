"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { listBots, type Bot } from "@/lib/api";
import { Badge, Sparkline, Skeleton } from "@/components/ui";
import { makeSeries } from "@/lib/sample";

function botSubtitle(bot: Bot): string {
  const c = bot.config as {
    trail_pct?: number;
    initial_shares?: number;
    stop_pct?: number;
    politician?: string;
  };
  const parts: string[] = [];
  if (typeof c.politician === "string") parts.push(c.politician);
  if (typeof c.trail_pct === "number")
    parts.push(`${(c.trail_pct * 100).toFixed(0)}% trail`);
  if (typeof c.initial_shares === "number") parts.push(`${c.initial_shares} sh`);
  if (typeof c.stop_pct === "number")
    parts.push(`stop ${(c.stop_pct * 100).toFixed(0)}%`);
  const cadence =
    bot.schedule_cadence_sec % 60 === 0
      ? `checks every ${bot.schedule_cadence_sec / 60}m`
      : `checks every ${bot.schedule_cadence_sec}s`;
  parts.push(cadence);
  return parts.join(" · ");
}

function botTitle(bot: Bot): string {
  const symbol = (bot.config as { symbol?: string }).symbol;
  const kind =
    bot.strategy_type === "trailing_stop"
      ? "Trailing-stop"
      : bot.strategy_type === "copy_trade"
        ? "Copy-trade"
        : bot.strategy_type;
  return symbol ? `${kind} · ${symbol}` : `${kind} · ${bot.name}`;
}

export function ActiveBots({
  action,
  compact,
}: {
  action?: React.ReactNode;
  compact?: boolean;
}) {
  const [bots, setBots] = useState<Bot[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await listBots();
        if (!cancelled) setBots(data);
      } catch (e) {
        if (!cancelled)
          setError(e instanceof Error ? e.message : "Could not load bots.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (compact) {
    const note = (t: string) => (
      <div className="mw-lbl" style={{ textTransform: "none", letterSpacing: 0 }}>
        {t}
      </div>
    );
    return (
      <div className="widget">
        <div className="wh">
          <span className="wht">
            Active bots{!loading && !error ? ` · ${bots.length}` : ""}
          </span>
        </div>
        {loading && note("Loading…")}
        {!loading && error && note("Couldn’t load bots.")}
        {!loading && !error && bots.length === 0 && note("No bots yet.")}
        {!loading &&
          !error &&
          bots.map((bot) => {
            const paused = bot.status !== "active";
            return (
              <Link key={bot.id} href={`/bots/${bot.id}`} className="mrow">
                <span className={`bdot${paused ? " paused" : ""}`} />
                <span className="mn">{botTitle(bot)}</span>
                <Badge tone={paused ? "n" : "g"}>{bot.status}</Badge>
              </Link>
            );
          })}
      </div>
    );
  }

  return (
    <>
      <div className="sec-h" style={{ margin: "0 0 13px" }}>
        <h2>
          Active bots{" "}
          {!loading && !error && <span className="cnt">· {bots.length}</span>}
        </h2>
        {action}
      </div>
      <div className="tcard">
        {loading && (
          <div className="botrow" style={{ cursor: "default" }}>
            <Skeleton w={8} h={8} r={4} />
            <div className="binfo">
              <Skeleton w={180} h={12} />
              <Skeleton w={260} h={10} style={{ marginTop: 8 }} />
            </div>
          </div>
        )}

        {!loading && error && (
          <div className="botrow" style={{ cursor: "default" }}>
            <div className="binfo">
              <div className="bsub neg">Could not load bots — {error}</div>
            </div>
          </div>
        )}

        {!loading && !error && bots.length === 0 && (
          <div className="botrow" style={{ cursor: "default" }}>
            <div className="binfo">
              <div className="bname">No bots yet</div>
              <div className="bsub">Create one to automate a trailing stop.</div>
            </div>
          </div>
        )}

        {!loading &&
          !error &&
          bots.map((bot) => {
            const paused = bot.status !== "active";
            return (
              <Link key={bot.id} href={`/bots/${bot.id}`} className="botrow clickable">
                <span className={`bdot${paused ? " paused" : ""}`} />
                <div className="binfo">
                  <div className="bname">{botTitle(bot)}</div>
                  <div className="bsub">{botSubtitle(bot)}</div>
                </div>
                <Sparkline
                  data={makeSeries(28, 20, paused ? 0 : 8, bot.id + 2)}
                  tone={paused ? "loss" : "gain"}
                />
                <div className="bpl">
                  <Badge tone={paused ? "n" : "g"}>{bot.status}</Badge>
                </div>
              </Link>
            );
          })}
      </div>
    </>
  );
}
