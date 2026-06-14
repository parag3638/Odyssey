"use client";

import { useRouter } from "next/navigation";
import type { StockRow } from "@/lib/api";
import { compact, money, pct } from "@/lib/format";
import { DataTable, type Column, Pill, Tag, TickerLogo } from "@/components/ui";
import { StarIcon } from "@/components/icons";

export function StocksTable({
  rows,
  loading,
  isStarred,
  onToggleStar,
  empty = "No stocks match.",
  initialSort = { key: "company", dir: "asc" as const },
  pageSize,
  minimal,
}: {
  rows: StockRow[];
  loading?: boolean;
  isStarred?: (s: string) => boolean;
  onToggleStar?: (s: string) => void;
  empty?: string;
  initialSort?: { key: string; dir: "asc" | "desc" };
  pageSize?: number;
  /** drop Sector/Industry columns — for the narrow home preview */
  minimal?: boolean;
}) {
  const router = useRouter();

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

  columns.push({
    key: "company",
    header: <>Company <span className="faint">· {rows.length}</span></>,
    align: "l",
    sortable: true,
    sortValue: (r) => r.symbol,
    render: (r) => (
      <div className="sym">
        <TickerLogo symbol={r.symbol} logo={r.logo_url || undefined} />
        <span>
          <span className="tk">{r.symbol}</span>
          {r.name && <span className="nm3">{r.name}</span>}
        </span>
      </div>
    ),
  });

  if (!minimal) {
    columns.push(
      {
        key: "sector",
        header: "Sector",
        align: "l",
        sortable: true,
        sortValue: (r) => r.sector,
        render: (r) => (r.sector ? <Tag muted>{r.sector}</Tag> : <span className="faint">—</span>),
      },
      {
        key: "industry",
        header: "Industry",
        align: "l",
        sortable: true,
        sortValue: (r) => r.industry,
        render: (r) => <span className="faint">{r.industry || "—"}</span>,
      },
    );
  }

  columns.push(
    {
      key: "price",
      header: "Price",
      sortable: true,
      sortValue: (r) => r.price ?? -1,
      render: (r) => <span className="tnum">{r.price != null ? money(r.price) : "—"}</span>,
    },
    {
      key: "change_pct",
      header: "1D",
      sortable: true,
      sortValue: (r) => r.change_pct ?? -1e9,
      render: (r) =>
        r.change_pct == null ? (
          <span className="faint">—</span>
        ) : (
          <Pill tone={r.change_pct >= 0 ? "g" : "r"}>{pct(r.change_pct)}</Pill>
        ),
    },
    {
      key: "market_cap",
      header: "Mkt cap",
      sortable: true,
      sortValue: (r) => r.market_cap ?? -1,
      render: (r) => (
        <span className="tnum">{r.market_cap != null ? compact(r.market_cap) : "—"}</span>
      ),
    },
  );

  return (
    <DataTable<StockRow>
      columns={columns}
      rows={rows}
      rowKey={(r) => r.symbol}
      loading={loading}
      onRowClick={(r) => router.push(`/stocks/${r.symbol}`)}
      initialSort={initialSort}
      empty={empty}
      pageSize={pageSize}
    />
  );
}
