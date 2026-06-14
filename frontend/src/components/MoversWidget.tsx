"use client";

import { useRouter } from "next/navigation";
import { pct } from "@/lib/format";
import { Pill } from "@/components/ui";

export interface MoverRow {
  symbol: string;
  name?: string;
  change_pct: number | null;
}

export function MoversWidget({
  gainers,
  losers,
}: {
  gainers: MoverRow[];
  losers: MoverRow[];
}) {
  const router = useRouter();
  const row = (i: MoverRow) => (
    <div className="mrow" key={i.symbol} onClick={() => router.push(`/stocks/${i.symbol}`)}>
      <span className="ms">{i.symbol}</span>
      <span className="mn">{i.name || ""}</span>
      <Pill tone={(i.change_pct ?? 0) >= 0 ? "g" : "r"}>
        {i.change_pct != null ? pct(i.change_pct) : "—"}
      </Pill>
    </div>
  );
  return (
    <div className="widget">
      <div className="wh">
        <span className="wht">Market movers</span>
      </div>
      <div className="mw-lbl">▲ Gainers</div>
      {gainers.slice(0, 4).map(row)}
      <div className="mw-lbl">▼ Losers</div>
      {losers.slice(0, 4).map(row)}
    </div>
  );
}
