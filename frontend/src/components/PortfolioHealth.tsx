"use client";

import { useEffect, useState } from "react";
import { SparklesIcon } from "@/components/icons";
import { getPortfolioHealth, type PortfolioHealthOut } from "@/lib/api";

/* Portfolio health — concentration/overlap observer. Advisory information only;
   self-hides when there's no key, no connected account, or no holdings. */
export function PortfolioHealth({ refreshKey }: { refreshKey?: string }) {
  const [data, setData] = useState<PortfolioHealthOut | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const d = await getPortfolioHealth();
        if (!cancelled) setData(d);
      } catch {
        if (!cancelled) setData(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  if (!data?.available || !data.text) return null;

  return (
    <div className="pfhealth">
      <div className="pfh-head">
        <SparklesIcon /> Portfolio health
      </div>
      <div className="pfh-text">{data.text}</div>
      {data.concentrations.length > 0 && (
        <div className="pfh-bars">
          {data.concentrations.map((c) => (
            <div className="pfh-row" key={c.label}>
              <span className="k">{c.label}</span>
              <span className="allocbar">
                <span style={{ flexGrow: c.weight_pct, background: "var(--text-3)" }} />
                <span style={{ flexGrow: 100 - c.weight_pct, background: "var(--card-3)" }} />
              </span>
              <span className="v tnum">{c.weight_pct.toFixed(1)}%</span>
            </div>
          ))}
        </div>
      )}
      {data.disclaimer && <div className="nsfoot">{data.disclaimer}</div>}
    </div>
  );
}
