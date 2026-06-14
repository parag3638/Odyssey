"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import type { StockRow } from "@/lib/api";
import type { HoldingView } from "@/lib/types";
import { money, pct, qtyFmt } from "@/lib/format";
import { ExpandIcon } from "@/components/icons";
import { Pill, SegmentedControl, TickerLogo } from "@/components/ui";

interface Row {
  symbol: string;
  sub: string;
  price: number | null;
  changePct: number | null;
  logo?: string;
}

export function HoldingsRail({
  holdings,
  watchlist,
}: {
  holdings: HoldingView[];
  watchlist: StockRow[];
}) {
  const [view, setView] = useState<"holdings" | "watchlist">("holdings");
  const router = useRouter();

  const rows: Row[] =
    view === "holdings"
      ? holdings.map((h) => {
          const price = h.price ?? h.avgCost;
          return {
            symbol: h.symbol,
            sub: `${qtyFmt(h.qty)} shares`,
            price,
            changePct:
              h.prevClose && h.prevClose > 0
                ? ((price - h.prevClose) / h.prevClose) * 100
                : null,
          };
        })
      : watchlist.map((w) => ({
          symbol: w.symbol,
          sub: w.name || "",
          price: w.price,
          changePct: w.change_pct,
          logo: w.logo_url || undefined,
        }));

  return (
    <div className="widget">
      <div className="wh">
        <SegmentedControl
          options={[
            { label: "Holdings", value: "holdings" },
            { label: "Watchlist", value: "watchlist" },
          ]}
          value={view}
          onChange={setView}
        />
        <a
          onClick={() => router.push(view === "holdings" ? "/positions" : "/watchlist")}
          aria-label="Expand"
        >
          <ExpandIcon />
        </a>
      </div>
      {rows.length === 0 ? (
        <div className="mw-lbl" style={{ textTransform: "none", letterSpacing: 0 }}>
          {view === "holdings" ? "No holdings yet." : "No stocks followed yet."}
        </div>
      ) : (
        rows.slice(0, 7).map((r) => (
          <div key={r.symbol} className="hrow" onClick={() => router.push(`/stocks/${r.symbol}`)}>
            <TickerLogo symbol={r.symbol} size="sm" logo={r.logo} />
            <div className="hi">
              <div className="hs">{r.symbol}</div>
              <div className="hsub">{r.sub}</div>
            </div>
            <div className="hr">
              <div className="hpx">{r.price != null ? money(r.price) : "—"}</div>
              {r.changePct != null && (
                <Pill tone={r.changePct >= 0 ? "g" : "r"}>{pct(r.changePct)}</Pill>
              )}
            </div>
          </div>
        ))
      )}
    </div>
  );
}
