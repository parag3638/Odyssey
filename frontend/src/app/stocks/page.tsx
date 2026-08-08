"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Nav } from "@/components/Nav";
import { StockFinderTable } from "@/components/StockFinderTable";
import { IndustryFilter, type FilterState } from "@/components/IndustryFilter";
import {
  getStockIndustries,
  getStockMetrics,
  listStocks,
  type IndustryRow,
  type StockMetricsMap,
  type StockRow,
} from "@/lib/api";
import { NlScreener, type ParsedScreen } from "@/components/NlScreener";
import { applyScreen, needsMetrics } from "@/lib/screenFilter";
import { useFillRows } from "@/lib/useFillRows";
import { SailIcon } from "@/components/icons";

export default function StocksPage() {
  const [industries, setIndustries] = useState<IndustryRow[]>([]);
  const [rows, setRows] = useState<StockRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<FilterState>({ sector: "", industry: "", q: "" });
  const [parsed, setParsed] = useState<ParsedScreen | null>(null);
  const [aiMetrics, setAiMetrics] = useState<StockMetricsMap>({});

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

  // A parsed screen that mentions fundamentals needs the metrics batch (server
  // caps at 60 symbols/request and caches each 6h).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!parsed || !needsMetrics(parsed.filters, parsed.sortField)) {
        if (!cancelled) setAiMetrics({});
        return;
      }
      try {
        const m = await getStockMetrics(rows.slice(0, 60).map((r) => r.symbol));
        if (!cancelled) setAiMetrics(m);
      } catch {
        if (!cancelled) setAiMetrics({});
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [parsed, rows]);

  const shown = useMemo(() => {
    const q = filter.q.trim().toUpperCase();
    let out = rows;
    if (q) {
      out = out.filter((r) => r.symbol.includes(q) || r.name.toUpperCase().includes(q));
    }
    if (parsed && (parsed.filters.length > 0 || parsed.sortField)) {
      out = applyScreen(out, parsed.filters, aiMetrics, parsed.sortField, parsed.sortDir);
    }
    return out;
  }, [rows, filter.q, parsed, aiMetrics]);

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
          <NlScreener parsed={parsed} onParsed={setParsed} />
        </div>

        <div className="reveal" style={{ ["--i" as string]: 2 }}>
          <IndustryFilter
            industries={industries}
            value={filter}
            onChange={(patch) => setFilter((f) => ({ ...f, ...patch }))}
          />
        </div>

        <div className="reveal" style={{ ["--i" as string]: 3 }} ref={tableRef}>
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
