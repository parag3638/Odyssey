"use client";

import { useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { logoColor, logoUrl, pct, signClass, signedMoney } from "@/lib/format";

/* ---------------- Card ---------------- */
export function Card({
  children,
  pad,
  flat,
  className = "",
  style,
  onClick,
}: {
  children: ReactNode;
  pad?: boolean;
  flat?: boolean;
  className?: string;
  style?: CSSProperties;
  onClick?: () => void;
}) {
  return (
    <div
      className={`card${pad ? " pad" : ""}${flat ? " flat" : ""} ${className}`}
      style={style}
      onClick={onClick}
    >
      {children}
    </div>
  );
}

/* ---------------- Pill / Tag / Badge ---------------- */
export function Pill({
  tone = "n",
  children,
}: {
  tone?: "g" | "r" | "n" | "o-g" | "o-r";
  children: ReactNode;
}) {
  return <span className={`pill ${tone}`}>{children}</span>;
}

export function Tag({
  children,
  dot,
  muted,
}: {
  children: ReactNode;
  dot?: boolean;
  muted?: boolean;
}) {
  return (
    <span className={`tag${muted ? " muted" : ""}`}>
      {dot && <span className="d" />}
      {children}
    </span>
  );
}

export function Badge({
  children,
  tone = "n",
}: {
  children: ReactNode;
  tone?: "g" | "r" | "n";
}) {
  return <span className={`badge ${tone}`}>{children}</span>;
}

/* Signed money + percent pill — the holdings-table return cell (ref #8). */
export function ReturnBadge({
  amount,
  percent,
}: {
  amount?: number;
  percent: number;
}) {
  const tone = percent >= 0 ? "g" : "r";
  return (
    <span className="twoparts">
      {amount !== undefined && (
        <span className={`${signClass(amount)} tnum`}>{signedMoney(amount)}</span>
      )}
      <Pill tone={tone}>{pct(percent)}</Pill>
    </span>
  );
}

/* ---------------- Ticker logo (letter-badge fallback) ---------------- */
export function TickerLogo({
  symbol,
  color,
  square,
  size,
  logo,
}: {
  symbol: string;
  color?: string;
  square?: boolean;
  size?: "sm" | "md";
  /** explicit logo URL (e.g. Finnhub); falls back to a CDN by symbol, then a letter badge */
  logo?: string;
}) {
  const [failed, setFailed] = useState(false);
  const cls = `lg${square ? " lgsq" : ""}${size === "sm" ? " sm" : ""}`;
  const src = logo || logoUrl(symbol);
  if (src && !failed) {
    return (
      <span className={cls} style={{ background: "var(--card-3)" }} aria-hidden>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={src} alt="" loading="lazy" onError={() => setFailed(true)} />
      </span>
    );
  }
  return (
    <span className={cls} style={{ background: color ?? logoColor(symbol) }} aria-hidden>
      {symbol.charAt(0)}
    </span>
  );
}

/* ---------------- Account-type icon tile (ref #6) ---------------- */
export function IconTile({
  color,
  sm,
  children,
}: {
  color: string;
  sm?: boolean;
  children: ReactNode;
}) {
  return (
    <span
      className={`itile${sm ? " sm" : ""}`}
      style={{ ["--tc" as string]: color }}
    >
      {children}
    </span>
  );
}

/* ---------------- Allocation donut indicator (ref #7) ---------------- */
export function Donut({
  percent,
  color = "var(--gain)",
}: {
  percent: number;
  color?: string;
}) {
  const r = 6;
  const circ = 2 * Math.PI * r;
  const dash = Math.max(0, Math.min(100, percent)) / 100;
  return (
    <svg className="donut" viewBox="0 0 16 16" aria-hidden>
      <circle className="track" cx="8" cy="8" r={r} />
      <circle
        className="val"
        cx="8"
        cy="8"
        r={r}
        style={{ stroke: color }}
        strokeDasharray={`${(dash * circ).toFixed(2)} ${circ.toFixed(2)}`}
      />
    </svg>
  );
}

/* ---------------- Switch ---------------- */
export function Switch({
  on,
  onToggle,
  label,
}: {
  on: boolean;
  onToggle: () => void;
  label?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      className={`switch${on ? " on" : ""}`}
      onClick={onToggle}
    />
  );
}

/* ---------------- Skeleton ---------------- */
export function Skeleton({
  w = "100%",
  h = 14,
  r = 8,
  style,
}: {
  w?: number | string;
  h?: number | string;
  r?: number;
  style?: CSSProperties;
}) {
  return (
    <div
      className="skeleton"
      style={{ width: w, height: h, borderRadius: r, ...style }}
    />
  );
}

/* ---------------- Empty state ---------------- */
export function EmptyState({
  icon,
  title,
  desc,
  action,
}: {
  icon?: ReactNode;
  title: string;
  desc?: string;
  action?: ReactNode;
}) {
  return (
    <div className="emptystate">
      {icon && <div className="ei">{icon}</div>}
      <div className="et">{title}</div>
      {desc && <div className="ed">{desc}</div>}
      {action && <div style={{ marginTop: 16 }}>{action}</div>}
    </div>
  );
}

/* ---------------- Stat / StatGrid ---------------- */
export function StatGrid({ children }: { children: ReactNode }) {
  return <div className="statgrid">{children}</div>;
}

export function Stat({
  value,
  label,
  tone = "",
  hl,
}: {
  value: ReactNode;
  label: string;
  tone?: "pos" | "neg" | "";
  hl?: boolean;
}) {
  return (
    <div className={`stat${hl ? " hl" : ""}`}>
      <div className={`pv tnum ${tone}`}>{value}</div>
      <div className="pk">{label}</div>
    </div>
  );
}
