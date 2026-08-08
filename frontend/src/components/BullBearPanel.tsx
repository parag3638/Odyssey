"use client";

import { useEffect, useState } from "react";
import { getBullBear, type BullBearOut } from "@/lib/api";

/* Strip inline [id] citation markers from a displayed phrase. */
const clean = (s: string) => s.replace(/\s?\[[a-z]+\d*\]/g, "");

/* Bull-vs-bear synthesis panel. Self-fetching and self-hiding — renders nothing
   when the LLM is unavailable or produced no points. Sits below the KPI strip. */
export function BullBearPanel({ symbol }: { symbol: string }) {
  const [data, setData] = useState<BullBearOut | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const d = await getBullBear(symbol);
        if (!cancelled) setData(d);
      } catch {
        if (!cancelled) setData(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  if (!data?.available || (data.bull.length === 0 && data.bear.length === 0)) return null;

  return (
    <div className="bbpanel reveal">
      <div className="bbhead">Bull vs bear</div>
      <div className="bbcols">
        <div className="bbcol">
          <span className="bbtag g">Bull</span>
          <ul>
            {data.bull.map((p, i) => (
              <li key={i}>{clean(p)}</li>
            ))}
          </ul>
        </div>
        <div className="bbcol">
          <span className="bbtag r">Bear</span>
          <ul>
            {data.bear.map((p, i) => (
              <li key={i}>{clean(p)}</li>
            ))}
          </ul>
        </div>
      </div>
      {data.crux && (
        <div className="bbcrux">
          <span className="k">Crux</span>
          {clean(data.crux)}
        </div>
      )}
      {data.citations.length > 0 && (
        <div className="aisum-cites">
          {data.citations.map((c) => (
            <a
              key={c.id}
              className="aisum-chip"
              href={c.url}
              target="_blank"
              rel="noopener noreferrer"
            >
              {c.label}
            </a>
          ))}
        </div>
      )}
    </div>
  );
}
