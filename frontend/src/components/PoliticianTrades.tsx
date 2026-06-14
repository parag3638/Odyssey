"use client";

import { useRouter } from "next/navigation";
import { initials } from "@/lib/format";
import type { Signal } from "@/lib/api";

export function PoliticianTrades({ signals }: { signals: Signal[] }) {
  const router = useRouter();
  return (
    <div className="widget">
      <div className="wh">
        <span className="wht">Politician trades</span>
        <a onClick={() => router.push("/signals")}>All →</a>
      </div>
      {signals.slice(0, 6).map((s) => {
        const buy = s.tx_type.toLowerCase() !== "sell";
        return (
          <div className="mrow" key={s.id} onClick={() => router.push(`/stocks/${s.symbol}`)}>
            <span className="lg sm" style={{ background: "var(--card-3)", color: "var(--text-2)" }}>
              {initials(s.politician)}
            </span>
            <span className="mn">{s.politician}</span>
            <span className="ms">{s.symbol}</span>
            <span className={`act ${buy ? "pos" : "neg"}`} style={{ fontSize: 11 }}>
              {buy ? "BUY" : "SELL"}
            </span>
          </div>
        );
      })}
      {signals.length === 0 && (
        <div className="mw-lbl" style={{ textTransform: "none", letterSpacing: 0 }}>
          No recent disclosures.
        </div>
      )}
    </div>
  );
}
