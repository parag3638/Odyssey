"use client";

import type { Citation } from "@/lib/api";

/* Source chips shown under any AI output. Several articles often share one
   publisher, so repeated labels get numbered ("benzinga 1", "benzinga 2") —
   each chip stays a distinct link instead of a row of identical-looking ones. */
export function CitationChips({ citations }: { citations: Citation[] }) {
  if (!citations || citations.length === 0) return null;

  const counts = new Map<string, number>();
  for (const c of citations) counts.set(c.label, (counts.get(c.label) ?? 0) + 1);
  const dupes = new Set([...counts].filter(([, n]) => n > 1).map(([label]) => label));

  const seen = new Map<string, number>();
  return (
    <div className="aisum-cites">
      {citations.map((c) => {
        const n = (seen.get(c.label) ?? 0) + 1;
        seen.set(c.label, n);
        return (
          <a
            key={c.id}
            className="aisum-chip"
            href={c.url}
            target="_blank"
            rel="noopener noreferrer"
            title={c.url}
          >
            {dupes.has(c.label) ? `${c.label} ${n}` : c.label}
          </a>
        );
      })}
    </div>
  );
}
