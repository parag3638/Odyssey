"use client";

import { useState } from "react";
import { SparklesIcon, XIcon } from "@/components/icons";
import { Button } from "@/components/ui";
import { parseScreen, type ScreenerFilter } from "@/lib/api";
import { chipLabel, sortLabel } from "@/lib/screenFilter";

export interface ParsedScreen {
  filters: ScreenerFilter[];
  sortField: string | null;
  sortDir: string;
  note: string | null;
}

/* Natural-language screener bar. The AI only translates the sentence into filter
   criteria — the deterministic engine (lib/screenFilter.ts) runs them — and the
   parsed criteria are shown as editable chips so nothing happens invisibly. */
export function NlScreener({
  parsed,
  onParsed,
}: {
  parsed: ParsedScreen | null;
  onParsed: (p: ParsedScreen | null) => void;
}) {
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  async function run() {
    const q = query.trim();
    if (!q || busy) return;
    setBusy(true);
    setMsg(null);
    try {
      const r = await parseScreen(q);
      if (!r.available) {
        setMsg("AI screening is unavailable right now.");
        onParsed(null);
      } else if (r.filters.length === 0 && !r.sort_field) {
        setMsg(r.note || "Couldn't turn that into filters — try naming a metric.");
        onParsed(null);
      } else {
        onParsed({
          filters: r.filters,
          sortField: r.sort_field,
          sortDir: r.sort_dir,
          note: r.note,
        });
      }
    } catch {
      setMsg("AI screening is unavailable right now.");
    } finally {
      setBusy(false);
    }
  }

  const removeFilter = (i: number) => {
    if (!parsed) return;
    const next = parsed.filters.filter((_, k) => k !== i);
    if (next.length === 0 && !parsed.sortField) onParsed(null);
    else onParsed({ ...parsed, filters: next });
  };

  return (
    <div className="nlscreen">
      <div className="nlscreen-bar">
        <SparklesIcon />
        <input
          className="nlscreen-input"
          placeholder="Describe a screen — e.g. profitable large-caps under 20x earnings"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              run();
            }
          }}
        />
        <Button variant="ghost" sm onClick={run} disabled={busy || !query.trim()}>
          {busy ? "Reading…" : "Start searching"}
        </Button>
      </div>

      {(parsed || msg) && (
        <div className="nlscreen-chips">
          {parsed?.filters.map((f, i) => (
            <button
              key={`${f.field}-${i}`}
              type="button"
              className="nlchip"
              onClick={() => removeFilter(i)}
              title="Remove this filter"
            >
              {chipLabel(f)}
              <XIcon />
            </button>
          ))}
          {parsed?.sortField && (
            <button
              type="button"
              className="nlchip"
              onClick={() => onParsed({ ...parsed, sortField: null })}
              title="Remove sort"
            >
              {sortLabel(parsed.sortField, parsed.sortDir)}
              <XIcon />
            </button>
          )}
          {parsed && (
            <button type="button" className="nlchip clear" onClick={() => onParsed(null)}>
              Clear
            </button>
          )}
          {(msg || parsed?.note) && <span className="nlscreen-note">{msg || parsed?.note}</span>}
        </div>
      )}
    </div>
  );
}
