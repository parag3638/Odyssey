"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getStockHistory, type StockRow } from "@/lib/api";
import { money, pct } from "@/lib/format";
import { Pill, TickerLogo } from "@/components/ui";
import { Sparkline } from "@/components/ui/LineChart";

/** Horizontal strip of stock cards each with a real 1M price-history sparkline. */
export function WatchlistStrip({ rows }: { rows: StockRow[] }) {
  const router = useRouter();
  const [hist, setHist] = useState<Record<string, number[]>>({});

  const syms = rows.slice(0, 8).map((r) => r.symbol).join(",");
  useEffect(() => {
    let cancelled = false;
    const list = syms ? syms.split(",") : [];
    Promise.all(
      list.map((s) =>
        getStockHistory(s, "1M")
          .then((h) => [s, h.map((p) => p.price)] as const)
          .catch(() => [s, [] as number[]] as const),
      ),
    ).then((pairs) => {
      if (!cancelled) setHist(Object.fromEntries(pairs));
    });
    return () => {
      cancelled = true;
    };
  }, [syms]);

  return (
    <div className="wstrip">
      {rows.slice(0, 8).map((r) => {
        const series = hist[r.symbol] || [];
        const up = (r.change_pct ?? 0) >= 0;
        return (
          <div className="wcard" key={r.symbol} onClick={() => router.push(`/stocks/${r.symbol}`)}>
            <div className="wt">
              <TickerLogo symbol={r.symbol} size="sm" />
              <span className="wsym">{r.symbol}</span>
              {r.change_pct != null && (
                <span style={{ marginLeft: "auto" }}>
                  <Pill tone={up ? "g" : "r"}>{pct(r.change_pct)}</Pill>
                </span>
              )}
            </div>
            <div className="wpx tnum">{r.price != null ? money(r.price) : "—"}</div>
            <div className="wspark">
              {series.length > 1 && (
                <Sparkline data={series} width={142} height={32} tone={up ? "gain" : "loss"} />
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
