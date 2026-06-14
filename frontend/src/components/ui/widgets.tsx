import type { ReactNode } from "react";
import { money } from "@/lib/format";
import type {
  AccountListItem,
  AllocationSlice,
  KpiItem,
} from "@/lib/types";
import { Card, IconTile } from "./primitives";
import {
  BankIcon,
  BriefcaseIcon,
  ChevronRightIcon,
  DiamondIcon,
  DollarIcon,
  LightningIcon,
} from "@/components/icons";

/* ---------------- KPI strip (ref #5) ---------------- */
export function KpiStrip({ items }: { items: KpiItem[] }) {
  return (
    <div className="kpis">
      {items.map((it) => (
        <div className="kpi" key={it.k}>
          <div className="k">{it.k}</div>
          <div className="v">{it.v}</div>
        </div>
      ))}
    </div>
  );
}

/* ---------------- Allocation bar + breakdown (ref #2) ---------------- */
export function AllocationBar({ slices }: { slices: AllocationSlice[] }) {
  const total = slices.reduce((s, x) => s + x.value, 0) || 1;
  return (
    <div className="allocbar">
      {slices.map((s) => (
        <span
          key={s.label}
          style={{ flexGrow: s.value / total, background: s.color }}
        />
      ))}
    </div>
  );
}

export function AssetBreakdown({
  title,
  slices,
  format = money,
}: {
  title: string;
  slices: AllocationSlice[];
  format?: (v: number) => string;
}) {
  return (
    <Card pad>
      <div className="breakdown">
        <div className="bk-title">{title}</div>
        <AllocationBar slices={slices} />
        <div className="alloc-legend">
          {slices.map((s) => (
            <div className="alloc-leg" key={s.label}>
              <span className="dot" style={{ background: s.color }} />
              <span className="nm">{s.label}</span>
              <span className="vv">{format(s.value)}</span>
            </div>
          ))}
        </div>
      </div>
    </Card>
  );
}

/* ---------------- Account list row (ref #2) ---------------- */
const ACCT_ICON: Record<AccountListItem["icon"], ReactNode> = {
  lightning: <LightningIcon />,
  diamond: <DiamondIcon />,
  briefcase: <BriefcaseIcon />,
  bank: <BankIcon />,
  dollar: <DollarIcon />,
};

export function AccountRow({
  item,
  onClick,
}: {
  item: AccountListItem;
  onClick?: () => void;
}) {
  return (
    <div className="acctrow" onClick={onClick}>
      <IconTile color={item.color}>{ACCT_ICON[item.icon]}</IconTile>
      <div className="ab">
        <div className="an">{item.name}</div>
        <div className="as">{item.sub}</div>
      </div>
      <div className="av tnum">{money(item.value)}</div>
      <span style={{ color: "var(--text-3)", display: "flex" }}>
        <ChevronRightIcon />
      </span>
    </div>
  );
}
