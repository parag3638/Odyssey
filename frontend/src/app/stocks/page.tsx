"use client";

import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { Nav } from "@/components/Nav";
import { StockFinderTable } from "@/components/StockFinderTable";
import { IndustryFilter, type FilterState } from "@/components/IndustryFilter";
import { getStockIndustries, listStocks, type IndustryRow, type StockRow } from "@/lib/api";
import { SailIcon } from "@/components/icons";

/* How many rows fit in the viewport below the table, so a page shows "all rows
   possible" before paginating. Measures the live row height + table offset and
   recomputes on resize. */
function useFillRows(ref: RefObject<HTMLDivElement | null>) {
  const [rows, setRows] = useState(15);
  useEffect(() => {
    const RESERVE = 180; // thead + pager + footer + breathing room
    const calc = () => {
      const el = ref.current;
      if (!el) return;
      const top = el.getBoundingClientRect().top;
      const rowEl = el.querySelector("tbody tr");
      const rowH = rowEl?.getBoundingClientRect().height || 52;
      const avail = window.innerHeight - top - RESERVE;
      setRows(Math.max(6, Math.floor(avail / rowH)));
    };
    calc();
    let raf = 0;
    const onResize = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(calc);
    };
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      cancelAnimationFrame(raf);
    };
  }, [ref]);
  return rows;
}

export default function StocksPage() {
  const [industries, setIndustries] = useState<IndustryRow[]>([]);
  const [rows, setRows] = useState<StockRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<FilterState>({ sector: "", industry: "", q: "" });

  const tableRef = useRef<HTMLDivElement | null>(null);
  const pageSize = useFillRows(tableRef);

  useEffect(() => {
    getStockIndustries().then(setIndustries).catch(() => setIndustries([]));
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const r = await listStocks({
          sector: filter.sector || undefined,
          industry: filter.industry || undefined,
          limit: 500,
        });
        if (!cancelled) setRows(r);
      } catch {
        if (!cancelled) setRows([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [filter.sector, filter.industry]);

  const shown = useMemo(() => {
    const q = filter.q.trim().toUpperCase();
    if (!q) return rows;
    return rows.filter(
      (r) => r.symbol.includes(q) || r.name.toUpperCase().includes(q),
    );
  }, [rows, filter.q]);

  return (
    <>
      <Nav active="stocks" accountLabel="my-paper" accountInitials="MY" />

      <div className="wrap roomy">
        <div className="shead reveal" style={{ ["--i" as string]: 0 }}>
          <span className="flame">
            <SailIcon />
          </span>
          <span className="ttl">Stocks</span>
          <span className="sub">{shown.length} of {rows.length} · curated universe</span>
        </div>

        <div className="reveal" style={{ ["--i" as string]: 1 }}>
          <IndustryFilter
            industries={industries}
            value={filter}
            onChange={(patch) => setFilter((f) => ({ ...f, ...patch }))}
          />
        </div>

        <div className="reveal" style={{ ["--i" as string]: 2 }} ref={tableRef}>
          <StockFinderTable
            key={`${filter.sector}|${filter.industry}`}
            rows={shown}
            loading={loading}
            empty="No stocks match your filters."
            pageSize={pageSize}
          />
        </div>
      </div>
    </>
  );
}
