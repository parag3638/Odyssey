"use client";

import { useMemo, useState } from "react";
import type { HoldingView } from "@/lib/types";
import { money, qtyFmt } from "@/lib/format";
import {
  ColumnCustomizer,
  type ColumnSpec,
  DataTable,
  type Column,
  Donut,
  EmptyState,
  IconButton,
  ReturnBadge,
  Tag,
  TickerLogo,
} from "@/components/ui";
import { FilterIcon, PositionsIcon } from "@/components/icons";

interface Derived extends HoldingView {
  cur: number;
  value: number;
  alloc: number;
  allAmt: number;
  allPct: number;
  todayAmt: number | null;
  todayPct: number | null;
}

function derive(h: HoldingView, totalValue: number): Derived {
  const cur = h.price ?? h.avgCost;
  const value = h.qty * cur;
  const cost = h.qty * h.avgCost;
  const allAmt = value - cost;
  const allPct = h.avgCost ? ((cur - h.avgCost) / h.avgCost) * 100 : 0;
  let todayAmt: number | null = null;
  let todayPct: number | null = null;
  if (h.prevClose != null && h.prevClose > 0) {
    todayPct = ((cur - h.prevClose) / h.prevClose) * 100;
    todayAmt = (cur - h.prevClose) * h.qty;
  }
  const alloc = totalValue > 0 ? (value / totalValue) * 100 : 0;
  return { ...h, cur, value, alloc, allAmt, allPct, todayAmt, todayPct };
}

const DEFAULT_COLUMNS: ColumnSpec[] = [
  { key: "holding", label: "Holdings", visible: true, locked: true },
  { key: "account", label: "Account", visible: true },
  { key: "alloc", label: "Allocation", visible: true },
  { key: "qty", label: "Quantity", visible: true },
  { key: "price", label: "Price", visible: true },
  { key: "today", label: "Today's return", visible: true },
  { key: "value", label: "Total value", visible: true },
  { key: "alltime", label: "All-time return", visible: true },
];

export function HoldingsTable({
  holdings,
  totalValue,
  loading,
  error,
  onRowClick,
}: {
  holdings: HoldingView[];
  totalValue: number;
  loading?: boolean;
  error?: string | null;
  onRowClick?: (h: HoldingView) => void;
}) {
  const [cols, setCols] = useState<ColumnSpec[]>(DEFAULT_COLUMNS);
  const [showFilters, setShowFilters] = useState(false);
  const [excluded, setExcluded] = useState<Set<string>>(new Set());

  const accounts = useMemo(
    () => Array.from(new Set(holdings.map((h) => h.account).filter(Boolean))) as string[],
    [holdings],
  );

  const rows = useMemo(() => {
    const filtered = holdings.filter(
      (h) => !h.account || !excluded.has(h.account),
    );
    return filtered.map((h) => derive(h, totalValue));
  }, [holdings, totalValue, excluded]);

  const visibleKeys = cols.filter((c) => c.visible).map((c) => c.key);

  const columns: Column<Derived>[] = [
    {
      key: "holding",
      header: "Holdings",
      align: "l",
      sortable: true,
      sortValue: (r) => r.symbol,
      render: (r) => (
        <div className="sym">
          <TickerLogo symbol={r.symbol} color={r.color} />
          <span>
            <span className="tk">{r.symbol}</span>
            {r.name && <span className="nm3">{r.name}</span>}
          </span>
        </div>
      ),
    },
    {
      key: "account",
      header: "Account",
      align: "l",
      render: (r) => (r.account ? <Tag dot>{r.account}</Tag> : <span className="faint">—</span>),
    },
    {
      key: "alloc",
      header: "Allocation",
      sortable: true,
      sortValue: (r) => r.alloc,
      render: (r) => (
        <span className="twoparts">
          <span className="tnum">{r.alloc.toFixed(2)}%</span>
          <Donut percent={r.alloc} />
        </span>
      ),
    },
    {
      key: "qty",
      header: "Quantity",
      sortable: true,
      sortValue: (r) => r.qty,
      render: (r) => <span className="tnum">{qtyFmt(r.qty)}</span>,
    },
    {
      key: "price",
      header: "Price",
      sortable: true,
      sortValue: (r) => r.cur,
      render: (r) => <span className="tnum">{money(r.cur)}</span>,
    },
    {
      key: "today",
      header: "Today's return",
      sortable: true,
      sortValue: (r) => r.todayPct ?? -Infinity,
      render: (r) =>
        r.todayPct == null ? (
          <span className="faint">—</span>
        ) : (
          <ReturnBadge amount={r.todayAmt ?? undefined} percent={r.todayPct} />
        ),
    },
    {
      key: "value",
      header: "Total value",
      sortable: true,
      sortValue: (r) => r.value,
      render: (r) => <span className="tnum">{money(r.value)}</span>,
    },
    {
      key: "alltime",
      header: "All-time return",
      sortable: true,
      sortValue: (r) => r.allPct,
      render: (r) => <ReturnBadge amount={r.allAmt} percent={r.allPct} />,
    },
  ];

  return (
    <div>
      <div className="sec-h" style={{ marginBottom: 12 }}>
        <h2>Holdings</h2>
        <div className="tbl-tools">
          {accounts.length > 1 && (
            <IconButton
              round
              aria-label="Filter by account"
              aria-expanded={showFilters}
              onClick={() => setShowFilters((s) => !s)}
            >
              <FilterIcon />
            </IconButton>
          )}
          <ColumnCustomizer columns={cols} onChange={setCols} />
        </div>
      </div>

      {showFilters && accounts.length > 1 && (
        <div className="filters">
          <span className="filter" style={{ color: "var(--text-3)" }}>
            Accounts
          </span>
          {accounts.map((a) => {
            const on = !excluded.has(a);
            return (
              <button
                key={a}
                type="button"
                className={`tag${on ? "" : " muted"}`}
                style={{ opacity: on ? 1 : 0.5 }}
                onClick={() =>
                  setExcluded((prev) => {
                    const next = new Set(prev);
                    if (next.has(a)) next.delete(a);
                    else next.add(a);
                    return next;
                  })
                }
              >
                <span className="d" />
                {a}
              </button>
            );
          })}
        </div>
      )}

      {error ? (
        <div className="tcard">
          <EmptyState
            icon={<PositionsIcon />}
            title="Could not load holdings"
            desc={error}
          />
        </div>
      ) : (
        <DataTable<Derived>
          columns={columns}
          rows={rows}
          rowKey={(r) => r.symbol}
          visibleKeys={visibleKeys}
          loading={loading}
          onRowClick={onRowClick ? (r) => onRowClick(r) : undefined}
          initialSort={{ key: "value", dir: "desc" }}
          empty="No holdings yet. Place an order to get started."
        />
      )}
    </div>
  );
}
