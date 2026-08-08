"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { listStocks, type StockRow } from "@/lib/api";
import { TickerLogo } from "@/components/ui";
import { SearchIcon, XIcon } from "@/components/icons";

/** Live symbol/name search over the full curated universe, with a results
 *  dropdown. Shared by /research (landing) and /research/[symbol] (pinned
 *  at top, so switching stocks never means leaving the page).
 *
 *  The results menu is portaled to <body> and positioned in fixed viewport
 *  coordinates (same technique as the Select component) — otherwise it gets
 *  trapped inside this input's local stacking context and can render behind
 *  later siblings on the page. */
export function ResearchSearch({
  autoFocus,
  onSelect,
}: {
  autoFocus?: boolean;
  onSelect: (symbol: string) => void;
}) {
  const [all, setAll] = useState<StockRow[]>([]);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ left: number; top: number; width: number } | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    listStocks({ limit: 500 }).then(setAll).catch(() => setAll([]));
  }, []);

  const matches = useMemo(() => {
    const query = q.trim().toUpperCase();
    if (!query) return [];
    return all
      .filter((r) => r.symbol.includes(query) || r.name.toUpperCase().includes(query))
      .slice(0, 8);
  }, [all, q]);

  const showMenu = open && matches.length > 0;

  useLayoutEffect(() => {
    if (!showMenu) return;
    const place = () => {
      const r = boxRef.current?.getBoundingClientRect();
      if (!r) return;
      setPos({ left: r.left, top: r.bottom + 8, width: r.width });
    };
    place();
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [showMenu]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!boxRef.current?.contains(t) && !menuRef.current?.contains(t)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  function pick(symbol: string) {
    setQ("");
    setOpen(false);
    onSelect(symbol);
  }

  return (
    <div className="rsearch" ref={boxRef}>
      <div className="searchbar" style={{ minWidth: 320 }}>
        <SearchIcon />
        <input
          placeholder="Search any stock by name or symbol"
          autoFocus={autoFocus}
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          style={{ height: "auto", border: 0, background: "transparent", padding: 0, flex: 1 }}
        />
        {q && (
          <button
            type="button"
            className="clr"
            aria-label="Clear search"
            onClick={() => setQ("")}
            style={{ color: "var(--text-3)", display: "flex" }}
          >
            <XIcon />
          </button>
        )}
      </div>

      {showMenu &&
        pos &&
        createPortal(
          <div
            ref={menuRef}
            className="rsearch-menu"
            role="listbox"
            style={{ left: pos.left, top: pos.top, width: pos.width }}
          >
            {matches.map((r) => (
              <button
                type="button"
                key={r.symbol}
                className="rsearch-row"
                role="option"
                aria-selected={false}
                onClick={() => pick(r.symbol)}
              >
                <div className="sym">
                  <TickerLogo symbol={r.symbol} logo={r.logo_url || undefined} />
                  <span>
                    <span className="tk">{r.symbol}</span>
                    {r.name && <span className="nm3">{r.name}</span>}
                  </span>
                </div>
              </button>
            ))}
          </div>,
          document.body,
        )}
    </div>
  );
}
