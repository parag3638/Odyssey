"use client";

import { useEffect, useState } from "react";
import { Skeleton } from "@/components/ui";
import { CitationChips } from "@/components/CitationChips";
import { getBullBear, type BullBearOut } from "@/lib/api";

/* Strip inline [id] citation markers from a displayed phrase. */
const clean = (s: string) => s.replace(/\s?\[[a-z]+\d*\]/g, "");

const SKEL_LINES = ["94%", "87%", "70%"];

/* Bull-vs-bear synthesis panel. Self-fetching and self-hiding — renders nothing
   when the LLM is unavailable or produced no points. Sits below the KPI strip. */
export function BullBearPanel({
  symbol,
  value,
  pending,
}: {
  symbol: string;
  value?: BullBearOut | null;
  pending?: boolean;
}) {
  const [data, setData] = useState<BullBearOut | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (value !== undefined) return;
    let cancelled = false;
    (async () => {
      setLoading(true);
      setData(null);
      try {
        const d = await getBullBear(symbol);
        if (!cancelled) setData(d);
      } catch {
        if (!cancelled) setData(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [symbol, value]);

  const shown = value !== undefined ? value : data;
  const isLoading = value !== undefined ? Boolean(pending) && !value?.available : loading;

  if (isLoading) {
    // Mirrors the loaded panel's structure (head, two tagged columns, crux
    // row) at roughly the same height, so nothing shifts when it arrives.
    return (
      <div className="bbpanel" aria-busy="true" aria-label="Loading bull vs bear">
        <div className="bbhead">Bull vs bear</div>
        <div className="bbcols">
          <div className="bbcol">
            <Skeleton w={40} h={18} r={999} style={{ marginBottom: 11 }} />
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {SKEL_LINES.map((w, i) => (
                <Skeleton key={i} w={w} h={11} />
              ))}
            </div>
          </div>
          <div className="bbcol">
            <Skeleton w={44} h={18} r={999} style={{ marginBottom: 11 }} />
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {SKEL_LINES.map((w, i) => (
                <Skeleton key={i} w={w} h={11} />
              ))}
            </div>
          </div>
        </div>
        <div className="bbcrux">
          <Skeleton w={190} h={9} r={5} />
        </div>
      </div>
    );
  }

  if (!shown?.available || (shown.bull.length === 0 && shown.bear.length === 0)) return null;

  return (
    <div className="bbpanel reveal">
      <div className="bbhead">Bull vs bear</div>
      <div className="bbcols">
        <div className="bbcol">
          <span className="bbtag g">Bull</span>
          <ul>
            {shown.bull.map((p, i) => (
              <li key={i}>{clean(p)}</li>
            ))}
          </ul>
        </div>
        <div className="bbcol">
          <span className="bbtag r">Bear</span>
          <ul>
            {shown.bear.map((p, i) => (
              <li key={i}>{clean(p)}</li>
            ))}
          </ul>
        </div>
      </div>
      {shown.crux && (
        <div className="bbcrux">
          <span className="k">Crux</span>
          {clean(shown.crux)}
        </div>
      )}
      <CitationChips citations={shown.citations} />
    </div>
  );
}
