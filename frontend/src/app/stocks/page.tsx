"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";
import { Nav } from "@/components/Nav";
import {
  StockFinderTable,
  type StockFinderSort,
} from "@/components/StockFinderTable";
import { IndustryFilter, type FilterState } from "@/components/IndustryFilter";
import { getStocksBootstrap } from "@/lib/api";
import { NlScreener, type ParsedScreen } from "@/components/NlScreener";
import { useFillRowsState } from "@/lib/useFillRows";
import { SailIcon } from "@/components/icons";

const SCREEN_SORT_KEYS: Record<string, string> = {
  marketCap: "mktCap",
  change_pct: "ret1d",
  revenue: "rev",
  revYoY: "revYoY",
  pe: "pe",
  eps: "eps",
  evSales: "evSales",
  sector: "sector",
};

export default function StocksPage() {
  const [filter, setFilter] = useState<FilterState>({ sector: "", industry: "", q: "" });
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [parsed, setParsed] = useState<ParsedScreen | null>(null);
  const [page, setPage] = useState(0);
  const [sort, setSort] = useState<StockFinderSort>({ key: "company", dir: "asc" });
  const [pageSize, setPageSize] = useState(0);

  const tableRef = useRef<HTMLDivElement | null>(null);
  const { rows: measuredPageSize, ready: layoutReady } = useFillRowsState(tableRef);

  useEffect(() => {
    if (!layoutReady || pageSize > 0) return;
    // The final reveal starts at 180ms and runs for 350ms. Freeze the measured
    // size after it settles so rendering real rows/pager cannot trigger a
    // second bootstrap request.
    const id = window.setTimeout(() => setPageSize(measuredPageSize), 550);
    return () => window.clearTimeout(id);
  }, [layoutReady, measuredPageSize, pageSize]);

  useEffect(() => {
    const id = window.setTimeout(() => {
      setDebouncedQuery(filter.q.trim());
      setPage(0);
    }, 250);
    return () => window.clearTimeout(id);
  }, [filter.q]);

  const effectiveSort = useMemo<StockFinderSort>(() => {
    if (!parsed?.sortField) return sort;
    return {
      key: SCREEN_SORT_KEYS[parsed.sortField] ?? parsed.sortField,
      dir: parsed.sortDir === "asc" ? "asc" : "desc",
    };
  }, [parsed, sort]);

  const requestKey = pageSize > 0
    ? JSON.stringify([
        page,
        pageSize,
        filter.sector,
        filter.industry,
        debouncedQuery,
        effectiveSort,
        parsed?.filters ?? [],
      ])
    : null;

  const { data, error, isLoading } = useSWR(
    requestKey ? `stocks-bootstrap:${requestKey}` : null,
    () =>
      getStocksBootstrap({
        page: page + 1,
        pageSize,
        sector: filter.sector || undefined,
        industry: filter.industry || undefined,
        q: debouncedQuery || undefined,
        sort: effectiveSort.key,
        direction: effectiveSort.dir,
        screenFilters: parsed?.filters,
      }),
    {
      keepPreviousData: true,
      dedupingInterval: 30_000,
      revalidateOnFocus: false,
    },
  );

  const total = data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / Math.max(pageSize, 1)));

  return (
    <>
      <Nav
        active="stocks"
        accountLabel="my-paper"
        accountInitials="MY"
        fetchAccount={false}
      />

      <div className="wrap roomy">
        <div className="shead reveal" style={{ ["--i" as string]: 0 }}>
          <span className="flame">
            <SailIcon />
          </span>
          <span className="ttl">Stocks</span>
          <span className="sub">{total} companies · curated universe</span>
        </div>

        <div className="reveal" style={{ ["--i" as string]: 1 }}>
          <NlScreener
            parsed={parsed}
            onParsed={(next) => {
              setParsed(next);
              setPage(0);
            }}
          />
        </div>

        <div className="reveal" style={{ ["--i" as string]: 2 }}>
          <IndustryFilter
            industries={data?.industries ?? []}
            value={filter}
            onChange={(patch) => {
              setFilter((current) => ({ ...current, ...patch }));
              if (patch.sector !== undefined || patch.industry !== undefined) setPage(0);
            }}
          />
        </div>

        <div className="reveal" style={{ ["--i" as string]: 3 }} ref={tableRef}>
          <StockFinderTable
            rows={data?.stocks ?? []}
            total={total}
            page={Math.min(page, pageCount - 1)}
            pageCount={pageCount}
            sort={effectiveSort}
            onPageChange={setPage}
            onSortChange={(next) => {
              setSort(next);
              if (parsed?.sortField) setParsed({ ...parsed, sortField: null });
              setPage(0);
            }}
            loading={!data && !error && (isLoading || pageSize === 0)}
            empty={error ? "Could not load stocks." : "No stocks match your filters."}
            pageSize={pageSize || measuredPageSize}
          />
        </div>
      </div>
    </>
  );
}
