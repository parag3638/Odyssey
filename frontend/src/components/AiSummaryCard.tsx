"use client";

import { useEffect, useState } from "react";
import { Skeleton } from "@/components/ui";
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
    return (
      <div className="newssum aisum" aria-busy="true">
        <div className="nsh">
          <span className="aisum-title">
            <SparklesIcon /> AI summary
          </span>
        </div>
        <Skeleton w="100%" h={13} />
        <Skeleton w="92%" h={13} style={{ marginTop: 9 }} />
        <Skeleton w="68%" h={13} style={{ marginTop: 9 }} />
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
      <div className="nstext">{data.text}</div>
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
      {data.disclaimer && <div className="nsfoot">{data.disclaimer}</div>}
    </div>
  );
}
