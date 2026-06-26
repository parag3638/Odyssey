"use client";

import { useRouter } from "next/navigation";
import { initials } from "@/lib/format";
import { Pill, Skeleton } from "@/components/ui";
import type { Signal } from "@/lib/api";

export function PoliticianTrades({
  signals,
  loading,
}: {
  signals: Signal[];
  loading?: boolean;
}) {
  const router = useRouter();
  const skeletonRow = (k: number) => (
    <div className="mrow" key={`sk${k}`} style={{ cursor: "default" }}>
      <Skeleton w={22} h={22} r={11} />
      <span className="mn">
        <Skeleton w={110} h={11} r={4} />
      </span>
      <Skeleton w={34} h={12} r={4} />
      <Skeleton w={44} h={20} r={7} />
    </div>
  );
  return (
    <div className="widget">
      <div className="wh">
        <span className="wht">Politician trades</span>
        <a onClick={() => router.push("/signals")}>All →</a>
      </div>
      {loading
        ? [0, 1, 2, 3, 4, 5].map(skeletonRow)
        : signals.slice(0, 6).map((s) => {
            const buy = s.tx_type.toLowerCase() !== "sell";
            return (
              <div className="mrow" key={s.id} onClick={() => router.push(`/stocks/${s.symbol}`)}>
                <span className="lg sm" style={{ background: "var(--card-3)", color: "var(--text-2)" }}>
                  {initials(s.politician)}
                </span>
                <span className="mn" style={{ color: "var(--text-2)" }}>{s.politician}</span>
                <span className="ms">{s.symbol}</span>
                <Pill tone={buy ? "o-g" : "o-r"}>{buy ? "BUY" : "SELL"}</Pill>
              </div>
            );
          })}
      {!loading && signals.length === 0 && (
        <div className="mw-lbl" style={{ textTransform: "none", letterSpacing: 0 }}>
          No recent disclosures.
        </div>
      )}
    </div>
  );
}
