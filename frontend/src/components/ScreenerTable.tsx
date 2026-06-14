"use client";

import { pct } from "@/lib/format";
import type { ScreenerRow } from "@/lib/types";
import {
  DataTable,
  type Column,
  Tag,
  TickerLogo,
  Button,
} from "@/components/ui";
import { SailIcon, SparklesIcon, XIcon } from "@/components/icons";

/* Natural-language stock screener — reference #9. */
export function ScreenerTable({
  rows,
  query = "Companies with over $10B in revenue and a 10% growth rate sorted by P/E",
}: {
  rows: ScreenerRow[];
  query?: string;
}) {
  const columns: Column<ScreenerRow>[] = [
    {
      key: "company",
      header: <>Company <span className="faint">· {rows.length}</span></>,
      align: "l",
      sortable: true,
      sortValue: (r) => r.symbol,
      render: (r) => (
        <div className="sym">
          <TickerLogo symbol={r.symbol} size="sm" />
          <span>
            <span className="tk">{r.symbol}</span>
            <span className="nm3">{r.name}</span>
          </span>
        </div>
      ),
    },
    {
      key: "sector",
      header: "Sector",
      align: "l",
      render: (r) => <Tag muted>{r.sector}</Tag>,
    },
    { key: "rev", header: "Rev.", sortable: true, sortValue: (r) => parseFloat(r.rev), render: (r) => <span className="tnum">{r.rev}</span> },
    {
      key: "revYoY",
      header: "Rev. (Y/Y)",
      sortable: true,
      sortValue: (r) => r.revYoY,
      render: (r) => <span className={`tnum ${r.revYoY >= 0 ? "pos" : "neg"}`}>{pct(r.revYoY)}</span>,
    },
    { key: "pe", header: "P/E", sortable: true, sortValue: (r) => r.pe, render: (r) => <span className="tnum">{r.pe.toFixed(2)}</span> },
    { key: "eps", header: "EPS", sortable: true, sortValue: (r) => r.eps, render: (r) => <span className="tnum">{r.eps.toFixed(2)}</span> },
    { key: "evSales", header: "EV/Sales", sortable: true, sortValue: (r) => r.evSales, render: (r) => <span className="tnum">{r.evSales.toFixed(2)}</span> },
    { key: "mktCap", header: "Mkt cap", sortable: true, sortValue: (r) => parseFloat(r.mktCap), render: (r) => <span className="tnum">{r.mktCap}</span> },
    {
      key: "ret1d",
      header: "1D return",
      sortable: true,
      sortValue: (r) => r.ret1d,
      render: (r) => <span className={`tnum ${r.ret1d >= 0 ? "pos" : "neg"}`}>{pct(r.ret1d)}</span>,
    },
  ];

  return (
    <div>
      <div className="shead">
        <span className="flame">
          <SailIcon />
        </span>
        <span className="ttl">Stock finder</span>
        <span className="sp" style={{ flex: 1 }} />
        <div className="nlquery">
          <span className="q">{query}</span>
          <button type="button" className="clr" aria-label="Clear query">
            <XIcon />
          </button>
        </div>
        <Button variant="ghost" sm>
          <SparklesIcon />
          Start searching
        </Button>
      </div>
      <DataTable<ScreenerRow>
        columns={columns}
        rows={rows}
        rowKey={(r) => r.symbol}
        initialSort={{ key: "pe", dir: "desc" }}
        onRowClick={() => {}}
      />
    </div>
  );
}
