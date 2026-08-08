"use client";

import { useEffect, useState } from "react";
import { SparklesIcon } from "@/components/icons";
import { getCongressContext, type AiResponse } from "@/lib/api";

/* Descriptive AI read of a stock's congressional trades — cluster detection +
   plain-English prose with disclosure links. Sits above the congressional table
   and self-hides when there's nothing to say (no key / no trades). */
export function CongressContext({ symbol }: { symbol: string }) {
  const [data, setData] = useState<AiResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const d = await getCongressContext(symbol);
        if (!cancelled) setData(d);
      } catch {
        if (!cancelled) setData(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  if (!data?.available || !data.text) return null;

  return (
    <div className="ccontext reveal">
      <div className="cc-head">
        <SparklesIcon /> AI read
      </div>
      <div className="cc-text">{data.text.replace(/\s?\[[a-z]+\d*\]/g, "")}</div>
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
