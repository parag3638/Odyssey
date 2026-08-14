"use client";

import { useRouter } from "next/navigation";
import { type StockRow } from "@/lib/api";
import { compactNum, pct } from "@/lib/format";
import {
  DataTable,
  type Column,
  Tag,
  TickerLogo,
} from "@/components/ui";

export type StockFinderSort = { key: string; dir: "asc" | "desc" };

/* Stock-finder table (reference #9): the full screener column set
   — Company · Sector · Rev. · Rev. (Y/Y) · P/E · Earnings · EPS · EV/Sales ·
   Mkt cap · 1D return — over the live universe. The backend embeds cached
   fundamentals in each paginated row, so rendering never waits on Finnhub. */
export function StockFinderTable({
  rows,
  total,
  page,
  pageCount,
  sort,
  onPageChange,
  onSortChange,
  loading,
  empty = "No stocks match your filters.",
  pageSize = 20,
}: {
  rows: StockRow[];
  total: number;
  page: number;
  pageCount: number;
  sort: StockFinderSort;
  onPageChange: (page: number) => void;
  onSortChange: (sort: StockFinderSort) => void;
  loading?: boolean;
  empty?: string;
  pageSize?: number;
}) {
  const router = useRouter();
  const cell = (v: number | null | undefined, fmt: (n: number) => string) => {
    if (v == null) return <span className="faint">—</span>;
    return <span className="tnum">{fmt(v)}</span>;
  };

  const signed = (v: number | null | undefined) => {
    if (v == null) return <span className="faint">—</span>;
    return <span className={`tnum ${v >= 0 ? "pos" : "neg"}`}>{pct(v)}</span>;
  };

  const columns: Column<StockRow>[] = [];

  columns.push(
    {
      key: "company",
      header: (
        <>
          Company <span className="faint">· {total}</span>
        </>
      ),
      align: "l",
      sortable: true,
      render: (r) => (
        <div className="sym">
          <TickerLogo symbol={r.symbol} logo={r.logo_url || undefined} size="sm" />
          <span>
            <span className="tk">{r.symbol}</span>
            {r.name && <span className="nm3">{r.name}</span>}
          </span>
        </div>
      ),
    },
    {
      key: "sector",
      header: "Sector",
      align: "l",
      sortable: true,
      render: (r) => (r.sector ? <Tag muted>{r.sector}</Tag> : <span className="faint">—</span>),
    },
    {
      key: "rev",
      header: "Rev.",
      sortable: true,
      render: (r) => cell(r.metrics?.revenue, compactNum),
    },
    {
      key: "revYoY",
      header: "Rev. (Y/Y)",
      sortable: true,
      render: (r) => signed(r.metrics?.revYoY),
    },
    {
      key: "pe",
      header: "P/E",
      sortable: true,
      render: (r) => cell(r.metrics?.pe, (n) => n.toFixed(2)),
    },
    {
      key: "earnings",
      header: "Earnings",
      render: (r) =>
        r.metrics?.earnings ? (
          <span>{r.metrics.earnings}</span>
        ) : (
          <span className="faint">—</span>
        ),
    },
    {
      key: "eps",
      header: "EPS",
      sortable: true,
      render: (r) => cell(r.metrics?.eps, (n) => n.toFixed(2)),
    },
    {
      key: "evSales",
      header: "EV/Sales",
      sortable: true,
      render: (r) => cell(r.metrics?.evSales, (n) => n.toFixed(2)),
    },
    {
      key: "mktCap",
      header: "Mkt cap",
      sortable: true,
      render: (r) => {
        const v = r.metrics?.marketCap ?? r.market_cap;
        return v == null ? (
          <span className="faint">—</span>
        ) : (
          <span className="tnum">{compactNum(v)}</span>
        );
      },
    },
    {
      key: "ret1d",
      header: "1D return",
      sortable: true,
      render: (r) =>
        r.change_pct == null ? (
          <span className="faint">—</span>
        ) : (
          <span className={`tnum ${r.change_pct >= 0 ? "pos" : "neg"}`}>{pct(r.change_pct)}</span>
        ),
    },
  );

  return (
    <DataTable<StockRow>
      columns={columns}
      rows={rows}
      rowKey={(r) => r.symbol}
      loading={loading}
      skeletonRows={Math.min(pageSize, 12)}
      onRowClick={(r) => router.push(`/stocks/${r.symbol}`)}
      empty={empty}
      sort={sort}
      onSortChange={onSortChange}
      page={page}
      pageCount={pageCount}
      total={total}
      onPageChange={onPageChange}
    />
  );
}
