"use client";

import { useRouter } from "next/navigation";
import { pct } from "@/lib/format";
import { Pill, Skeleton, Tag, TickerLogo } from "@/components/ui";

export interface MoverRow {
  symbol: string;
  name?: string;
  change_pct: number | null;
}

export function MoversWidget({
  gainers,
  losers,
  loading,
}: {
  gainers: MoverRow[];
  losers: MoverRow[];
  loading?: boolean;
}) {
  const router = useRouter();
  const row = (i: MoverRow) => (
    <div className="mrow" key={i.symbol} onClick={() => router.push(`/stocks/${i.symbol}`)}>
      <TickerLogo symbol={i.symbol} size="sm" />
      <span className="ms">{i.symbol}</span>
      <span className="mn">{i.name || ""}</span>
      <Pill tone={(i.change_pct ?? 0) >= 0 ? "g" : "r"}>
        {i.change_pct != null ? pct(i.change_pct) : "—"}
      </Pill>
    </div>
  );
  const skeletonRow = (k: number) => (
    <div className="mrow" key={`sk${k}`} style={{ cursor: "default" }}>
      <Skeleton w={22} h={22} r={11} />
      <Skeleton w={34} h={12} r={4} />
      <span className="mn">
        <Skeleton w={96} h={10} r={4} />
      </span>
      <Skeleton w={50} h={20} r={7} />
    </div>
  );
  return (
    <div className="widget">
      <div className="wh">
        <span className="wht">Market movers</span>
        <Tag muted>1D</Tag>
      </div>
      <div className="mw-lbl">
        <span style={{ color: "var(--gain)" }}>▲</span> Gainers
      </div>
      {loading ? [0, 1, 2].map(skeletonRow) : gainers.slice(0, 3).map(row)}
      <div className="mw-lbl">
        <span style={{ color: "var(--loss)" }}>▼</span> Losers
      </div>
      {loading ? [3, 4, 5].map(skeletonRow) : losers.slice(0, 3).map(row)}
    </div>
  );
}
