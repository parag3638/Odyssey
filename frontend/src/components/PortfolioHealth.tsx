"use client";

import { useEffect, useState } from "react";
import { SparklesIcon } from "@/components/icons";
import { getPositionsHealth, type PortfolioHealthOut } from "@/lib/api";

/* Portfolio health — concentration/overlap observer. Advisory information only;
   self-hides when there's no key, no connected account, or no holdings. */
export function PortfolioHealth({
  initialData,
  healthKey,
  pending,
}: {
  initialData?: PortfolioHealthOut | null;
  healthKey?: string | null;
  pending?: boolean;
}) {
  const [polled, setPolled] = useState<{
    key: string;
    value: PortfolioHealthOut;
  } | null>(null);

  useEffect(() => {
    if (!pending || !healthKey || initialData?.available) return;
    let cancelled = false;
    (async () => {
      for (let attempt = 0; attempt < 7 && !cancelled; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 2500));
        try {
          const next = await getPositionsHealth(healthKey);
          if (cancelled) return;
          setPolled({ key: healthKey, value: next });
          if (next.available) return;
        } catch {
          return;
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [healthKey, initialData, pending]);

  const visible = polled?.key === healthKey && polled.value.available
    ? polled.value
    : initialData;
  if (!visible?.available || !visible.text) return null;

  return (
    <div className="pfhealth">
      <div className="pfh-head">
        <SparklesIcon /> Portfolio health
      </div>
      <div className="pfh-text">{visible.text}</div>
      {visible.concentrations.length > 0 && (
        <div className="pfh-bars">
          {visible.concentrations.map((c) => (
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
      {visible.disclaimer && <div className="nsfoot">{visible.disclaimer}</div>}
    </div>
  );
}
