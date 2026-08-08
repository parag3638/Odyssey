"use client";

import { useEffect, useState } from "react";
import { Skeleton } from "@/components/ui";
import { CitationChips } from "@/components/CitationChips";
import { SparklesIcon } from "@/components/icons";
import { getAiSummary, type AiResponse } from "@/lib/api";

/* Grounded AI summary of a stock's recent news + analyst views. Self-fetching and
   self-hiding: renders nothing when the backend LLM is unconfigured or offline, so
   it's safe to drop into any stock/research view. Reuses the `.newssum` prose idiom
   with a sparkle header and source-link chips. */
export function AiSummaryCard({ symbol }: { symbol: string }) {
  const [data, setData] = useState<AiResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setData(null);
      try {
        const d = await getAiSummary(symbol);
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
  }, [symbol]);

  if (loading) {
    // Mirrors the loaded card's structure (header, prose block, chip row, footer)
    // at the same height, so nothing shifts when the summary arrives.
    return (
      <div className="newssum aisum" aria-busy="true" aria-label="Loading AI summary">
        <div className="nsh">
          <span className="aisum-title">
            <SparklesIcon /> AI summary
          </span>
          <span className="aisum-badge">Beta</span>
        </div>
        <div className="aisum-scroll is-skel">
          {["100%", "97%", "92%", "99%", "88%", "54%"].map((w, i) => (
            <Skeleton key={i} w={w} h={10} r={5} />
          ))}
          <div className="aisum-cites">
            <Skeleton w={64} h={18} r={999} />
            <Skeleton w={72} h={18} r={999} />
            <Skeleton w={58} h={18} r={999} />
          </div>
        </div>
        <div className="nsfoot">
          <Skeleton w={190} h={9} r={5} />
        </div>
      </div>
    );
  }

  if (!data?.available || !data.text) return null;

  return (
    <div className="newssum aisum reveal">
      <div className="nsh">
        <span className="aisum-title">
          <SparklesIcon /> AI summary
        </span>
        <span className="aisum-badge">Beta</span>
      </div>
      <div className="aisum-scroll">
        <div className="nstext">{data.text}</div>
        <CitationChips citations={data.citations} />
      </div>
      {data.disclaimer && <div className="nsfoot">{data.disclaimer}</div>}
    </div>
  );
}
