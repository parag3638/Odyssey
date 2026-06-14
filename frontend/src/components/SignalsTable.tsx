"use client";

import { useEffect, useState } from "react";
import { listSignals, type Signal } from "@/lib/api";
import { initials } from "@/lib/format";
import {
  Badge,
  DataTable,
  type Column,
  EmptyState,
  TickerLogo,
} from "@/components/ui";
import {
  ArrowDownRightIcon,
  ArrowUpRightIcon,
  SignalsIcon,
} from "@/components/icons";

function shortName(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length < 2) return name;
  return `${parts[0][0]}. ${parts[parts.length - 1]}`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function shortDate(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return iso || "—";
  return `${MONTHS[Number(m[2]) - 1] ?? m[2]} ${Number(m[3])}`;
}

function amountKey(r: string): number {
  const m = /([\d.]+)\s*([KMB]?)/i.exec((r || "").replace(/[$,]/g, ""));
  if (!m) return 0;
  let v = parseFloat(m[1]);
  const u = m[2].toUpperCase();
  if (u === "K") v *= 1e3;
  else if (u === "M") v *= 1e6;
  else if (u === "B") v *= 1e9;
  return v;
}

export function SignalsTable({
  politician,
  action,
}: {
  politician?: string;
  action?: "buy" | "sell";
}) {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const data = await listSignals(politician || undefined);
        if (!cancelled) setSignals(data);
      } catch {
        if (!cancelled) setError("Could not load signals.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [politician]);

  const rows = action
    ? signals.filter((s) => s.tx_type.toLowerCase() === action)
    : signals;

  const columns: Column<Signal>[] = [
    {
      key: "politician",
      header: <>Politician <span className="faint">· {rows.length}</span></>,
      align: "l",
      sortable: true,
      sortValue: (s) => s.politician,
      render: (s) => (
        <div className="sym">
          <span className="lg" style={{ background: "var(--card-3)", color: "var(--text-2)" }}>
            {initials(s.politician)}
          </span>
          <span className="tk">{shortName(s.politician)}</span>
        </div>
      ),
    },
    {
      key: "ticker",
      header: "Ticker",
      align: "l",
      sortable: true,
      sortValue: (s) => s.symbol,
      render: (s) => (
        <div className="sym">
          <TickerLogo symbol={s.symbol} size="sm" />
          <span className="tk">{s.symbol}</span>
        </div>
      ),
    },
    {
      key: "action",
      header: "Action",
      align: "l",
      sortable: true,
      sortValue: (s) => s.tx_type,
      render: (s) => {
        const buy = s.tx_type.toLowerCase() !== "sell";
        return (
          <span className={`act ${buy ? "pos" : "neg"}`}>
            {buy ? "BUY" : "SELL"}
            {buy ? <ArrowUpRightIcon /> : <ArrowDownRightIcon />}
          </span>
        );
      },
    },
    {
      key: "size",
      header: "Est. size",
      sortable: true,
      sortValue: (s) => amountKey(s.amount_range),
      render: (s) => <span className="tnum">{s.amount_range || "—"}</span>,
    },
    {
      key: "tx",
      header: "Tx date",
      sortable: true,
      sortValue: (s) => s.tx_date,
      render: (s) => <span className="tnum">{shortDate(s.tx_date)}</span>,
    },
    {
      key: "disclosed",
      header: "Disclosed",
      sortable: true,
      sortValue: (s) => s.disclosed_date,
      render: (s) => <span className="tnum">{shortDate(s.disclosed_date)}</span>,
    },
    {
      key: "status",
      header: "Status",
      align: "l",
      render: (s) =>
        s.source_url ? (
          <a
            href={s.source_url}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
          >
            <Badge>Disclosure ↗</Badge>
          </a>
        ) : (
          <Badge>New</Badge>
        ),
    },
  ];

  if (error) {
    return (
      <div className="tcard">
        <EmptyState icon={<SignalsIcon />} title="Could not load signals" desc={error} />
      </div>
    );
  }

  return (
    <DataTable<Signal>
      columns={columns}
      rows={rows}
      rowKey={(s) => String(s.id)}
      loading={loading}
      initialSort={{ key: "disclosed", dir: "desc" }}
      empty="No signals yet. Hit “Sync now” to pull the latest disclosures."
    />
  );
}
