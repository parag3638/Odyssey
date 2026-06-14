"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { getStockMetrics, type StockMetrics, type StockRow } from "@/lib/api";
import { compactNum, pct } from "@/lib/format";
import {
  DataTable,
  type Column,
  Skeleton,
  Tag,
  TickerLogo,
} from "@/components/ui";
import { StarIcon } from "@/components/icons";

type Sort = { key: string; dir: "asc" | "desc" };
type MetricsMap = Record<string, StockMetrics>;

const NEG = -Infinity;

/* Stock-finder table (reference #9): the full screener column set
   — Company · Sector · Rev. · Rev. (Y/Y) · P/E · Earnings · EPS · EV/Sales ·
   Mkt cap · 1D return — over the live universe. Fundamentals (everything past
   Sector that isn't price-derived) come from Finnhub and are fetched per page
   so a load stays within the free-tier rate limit; cells skeleton until their
   symbol resolves, then show the value or "—". Sort + pagination are owned here
   so we know which symbols a page needs. */
export function StockFinderTable({
  rows,
  loading,
  isStarred,
  onToggleStar,
  empty = "No stocks match your filters.",
  pageSize = 20,
}: {
  rows: StockRow[];
  loading?: boolean;
  isStarred?: (s: string) => boolean;
  onToggleStar?: (s: string) => void;
  empty?: string;
  pageSize?: number;
}) {
  const router = useRouter();
  const [sort, setSort] = useState<Sort>({ key: "company", dir: "asc" });
  const [page, setPage] = useState(0);
  const [metrics, setMetrics] = useState<MetricsMap>({});
  const requested = useRef<Set<string>>(new Set());

  const sorted = useMemo(() => {
    const dir = sort.dir === "asc" ? 1 : -1;
    const val = (r: StockRow): number | string => {
      const m = metrics[r.symbol];
      switch (sort.key) {
        case "company":
          return r.symbol;
        case "sector":
          return r.sector || "";
        case "ret1d":
          return r.change_pct ?? NEG;
        case "mktCap":
          return m?.marketCap ?? r.market_cap ?? NEG;
        case "rev":
          return m?.revenue ?? NEG;
        case "revYoY":
          return m?.revYoY ?? NEG;
        case "pe":
          return m?.pe ?? NEG;
        case "eps":
          return m?.eps ?? NEG;
        case "evSales":
          return m?.evSales ?? NEG;
        default:
          return r.symbol;
      }
    };
    return [...rows].sort((a, b) => {
      const va = val(a);
      const vb = val(b);
      if (typeof va === "number" && typeof vb === "number") return (va - vb) * dir;
      return String(va).localeCompare(String(vb)) * dir;
    });
  }, [rows, sort, metrics]);

  const pageCount = Math.max(1, Math.ceil(sorted.length / pageSize));
  const curPage = Math.min(page, pageCount - 1);
  const pageRows = useMemo(
    () => sorted.slice(curPage * pageSize, curPage * pageSize + pageSize),
    [sorted, curPage, pageSize],
  );

  // Jump back to the first page when the universe changes (e.g. a search filter
  // narrows `rows`). Adjusting state during render is React's recommended way to
  // react to a prop change — no cascading effect.
  const [prevRows, setPrevRows] = useState(rows);
  if (rows !== prevRows) {
    setPrevRows(rows);
    setPage(0);
  }

  // Enrich the visible page's symbols (those not already requested).
  useEffect(() => {
    const need = pageRows.map((r) => r.symbol).filter((s) => !requested.current.has(s));
    if (need.length === 0) return;
    need.forEach((s) => requested.current.add(s));
    let cancelled = false;
    getStockMetrics(need)
      .then((res) => {
        if (!cancelled) setMetrics((prev) => ({ ...prev, ...res }));
      })
      .catch(() => {
        // allow a later retry if the request failed
        need.forEach((s) => requested.current.delete(s));
      });
    return () => {
      cancelled = true;
    };
  }, [pageRows]);

  // A metric cell: skeleton until the symbol resolves, then value or "—".
  const cell = (sym: string, v: number | null | undefined, fmt: (n: number) => string) => {
    if (!(sym in metrics)) return <Skeleton w={46} h={11} style={{ marginLeft: "auto" }} />;
    if (v == null) return <span className="faint">—</span>;
    return <span className="tnum">{fmt(v)}</span>;
  };

  const signed = (sym: string, v: number | null | undefined) => {
    if (!(sym in metrics)) return <Skeleton w={56} h={11} style={{ marginLeft: "auto" }} />;
    if (v == null) return <span className="faint">—</span>;
    return <span className={`tnum ${v >= 0 ? "pos" : "neg"}`}>{pct(v)}</span>;
  };

  const columns: Column<StockRow>[] = [];

  if (onToggleStar) {
    columns.push({
      key: "star",
      header: "",
      align: "l",
      render: (r) => (
        <button
          type="button"
          className={`starbtn${isStarred?.(r.symbol) ? " on" : ""}`}
          aria-label={isStarred?.(r.symbol) ? "Remove from watchlist" : "Add to watchlist"}
          onClick={(e) => {
            e.stopPropagation();
            onToggleStar(r.symbol);
          }}
        >
          <StarIcon />
        </button>
      ),
    });
  }

  columns.push(
    {
      key: "company",
      header: (
        <>
          Company <span className="faint">· {rows.length}</span>
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
      render: (r) => cell(r.symbol, metrics[r.symbol]?.revenue, compactNum),
    },
    {
      key: "revYoY",
      header: "Rev. (Y/Y)",
      sortable: true,
      render: (r) => signed(r.symbol, metrics[r.symbol]?.revYoY),
    },
    {
      key: "pe",
      header: "P/E",
      sortable: true,
      render: (r) => cell(r.symbol, metrics[r.symbol]?.pe, (n) => n.toFixed(2)),
    },
    {
      key: "earnings",
      header: "Earnings",
      render: (r) =>
        !(r.symbol in metrics) ? (
          <Skeleton w={52} h={11} style={{ marginLeft: "auto" }} />
        ) : metrics[r.symbol]?.earnings ? (
          <span>{metrics[r.symbol].earnings}</span>
        ) : (
          <span className="faint">—</span>
        ),
    },
    {
      key: "eps",
      header: "EPS",
      sortable: true,
      render: (r) => cell(r.symbol, metrics[r.symbol]?.eps, (n) => n.toFixed(2)),
    },
    {
      key: "evSales",
      header: "EV/Sales",
      sortable: true,
      render: (r) => cell(r.symbol, metrics[r.symbol]?.evSales, (n) => n.toFixed(2)),
    },
    {
      key: "mktCap",
      header: "Mkt cap",
      sortable: true,
      render: (r) => {
        const v = metrics[r.symbol]?.marketCap ?? r.market_cap;
        // mkt cap can come from the row too, so only skeleton when neither is known yet
        if (v == null && !(r.symbol in metrics)) {
          return <Skeleton w={56} h={11} style={{ marginLeft: "auto" }} />;
        }
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
      rows={pageRows}
      rowKey={(r) => r.symbol}
      loading={loading}
      skeletonRows={Math.min(pageSize, 12)}
      onRowClick={(r) => router.push(`/stocks/${r.symbol}`)}
      empty={empty}
      sort={sort}
      onSortChange={(s) => {
        setSort(s);
        setPage(0); // re-sorting should jump back to the top
      }}
      page={curPage}
      pageCount={pageCount}
      total={sorted.length}
      onPageChange={setPage}
    />
  );
}
