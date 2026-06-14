"use client";

import { useId, useRef, useState } from "react";
import type { ReactNode } from "react";
import { money } from "@/lib/format";

const W = 1000;

/** Replace non-finite values (gaps/nulls/NaN from live data) by carrying the
    last finite value forward, keeping the array length so indices stay aligned. */
function sanitizeSeries(raw: number[]): number[] {
  const out: number[] = [];
  const seed = raw.find((v) => Number.isFinite(v));
  let last = Number.isFinite(seed) ? (seed as number) : 0;
  for (const v of raw) {
    if (Number.isFinite(v)) last = v;
    out.push(last);
  }
  return out;
}

interface LineChartProps {
  data: number[];
  height?: number;
  /** index where the line splits from neutral "past" to colored "now" */
  splitAt?: number;
  tone?: "gain" | "loss" | "auto" | "neutral";
  area?: boolean;
  volume?: boolean;
  volumes?: number[];
  baseline?: boolean;
  grid?: boolean;
  crosshair?: boolean;
  draw?: boolean;
  /** per-point labels for the tooltip / floating date */
  dates?: string[];
  /** "tooltip" = price/volume box (detail), "date" = floating date (hero) */
  hover?: "tooltip" | "date";
  /** signed pct label pinned to the end of the line (perf card) */
  pctEnd?: number;
  axis?: string[];
  className?: string;
  ariaLabel?: string;
}

export function LineChart({
  data: rawData,
  height = 300,
  splitAt,
  tone = "auto",
  area = true,
  volume = false,
  volumes,
  baseline = false,
  grid = false,
  crosshair = false,
  draw = false,
  dates,
  hover = "tooltip",
  pctEnd,
  axis,
  className = "",
  ariaLabel = "Price chart",
}: LineChartProps) {
  const boxRef = useRef<HTMLDivElement>(null);
  const [hi, setHi] = useState<number | null>(null);
  const gid = useId().replace(/:/g, "");

  // Live API data can contain gaps/nulls/NaN. Carry the last finite value
  // forward (same length) so geometry never yields NaN — React rejects NaN on
  // numeric SVG attributes like cx/cy.
  const data = sanitizeSeries(rawData);

  const n = data.length;
  const VBH = height;
  const min = data.length ? Math.min(...data) : 0;
  const max = data.length ? Math.max(...data) : 1;
  const top = 16;
  const bottom = VBH - (volume ? 34 : 18);
  const span = max - min || 1;
  const X = (i: number) => (n > 1 ? (i / (n - 1)) * W : W / 2);
  const Y = (v: number) => bottom - ((v - min) / span) * (bottom - top);

  const path = (lo: number, up: number) => {
    let d = `M${X(lo).toFixed(1)},${Y(data[lo]).toFixed(1)}`;
    for (let i = lo + 1; i <= up; i++)
      d += ` L${X(i).toFixed(1)},${Y(data[i]).toFixed(1)}`;
    return d;
  };

  const hasSplit = splitAt != null && splitAt > 0 && splitAt < n - 1;
  const ref = hasSplit ? splitAt! : 0;
  const nowColor =
    tone === "gain"
      ? "var(--gain)"
      : tone === "loss"
        ? "var(--loss)"
        : tone === "neutral"
          ? "var(--chart)"
          : data[n - 1] >= data[ref]
            ? "var(--gain)"
            : "var(--loss)";

  const pastPath = hasSplit ? path(0, ref) : null;
  const nowPath = hasSplit ? path(ref, n - 1) : path(0, n - 1);
  const areaPath = hasSplit
    ? `${nowPath} L${X(n - 1).toFixed(1)},${VBH} L${X(ref).toFixed(1)},${VBH} Z`
    : `${nowPath} L${W},${VBH} L0,${VBH} Z`;

  // volume bars
  const bw = (W / n) * 0.5;
  const volArea = 24;
  const volAt = (i: number) =>
    volumes ? volumes[i] : 5 + ((i * 37) % 30); // deterministic fallback
  const volMax = Math.max(...data.map((_, i) => volAt(i)), 1);

  const onMove = (e: React.MouseEvent) => {
    const el = boxRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const fx = (e.clientX - r.left) / r.width;
    setHi(Math.max(0, Math.min(n - 1, Math.round(fx * (n - 1)))));
  };

  const sx = hi != null ? X(hi) : 0;
  const sy = hi != null ? Y(data[hi]) : 0;
  const sxPct = (sx / W) * 100;
  const syPct = (sy / VBH) * 100;

  const fmtDate = (i: number) => dates?.[i] ?? `Point ${i + 1}`;

  let tip: ReactNode = null;
  if (crosshair && hi != null) {
    if (hover === "date") {
      tip = (
        <div
          className="cl-date"
          style={{ left: `clamp(46px, ${sxPct}%, calc(100% - 46px))` }}
        >
          {fmtDate(hi)}
        </div>
      );
    } else {
      const flip = sxPct > 62;
      tip = (
        <div
          className="cl-tip"
          style={{
            left: `${sxPct}%`,
            top: `clamp(6px, calc(${syPct}% - 64px), calc(100% - 80px))`,
            transform: flip ? "translateX(calc(-100% - 14px))" : "translateX(14px)",
          }}
        >
          <div className="dt">{fmtDate(hi)}</div>
          <div className="row">
            <span className="k" style={{ ["--kc" as string]: nowColor }}>
              Price
            </span>
            <span className="v">{money(data[hi])}</span>
          </div>
          {volume && (
            <div className="row">
              <span className="k">Volume</span>
              <span className="v">
                {(9000 + ((hi * 137) % 6000)).toLocaleString()}
              </span>
            </div>
          )}
        </div>
      );
    }
  }

  return (
    <>
      <div
        ref={boxRef}
        className={`chartbox${grid ? " grid" : ""} ${className}`}
        style={{ height }}
        onMouseMove={crosshair ? onMove : undefined}
        onMouseLeave={crosshair ? () => setHi(null) : undefined}
        role="img"
        aria-label={ariaLabel}
      >
        <svg
          className="cl-svg"
          viewBox={`0 0 ${W} ${VBH}`}
          preserveAspectRatio="none"
        >
          <defs>
            <linearGradient id={`g${gid}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor={nowColor} stopOpacity="0.16" />
              <stop offset="1" stopColor={nowColor} stopOpacity="0" />
            </linearGradient>
          </defs>

          {baseline && (
            <line
              className="cl-base"
              x1="0"
              x2={W}
              y1={Y(data[0])}
              y2={Y(data[0])}
              vectorEffect="non-scaling-stroke"
            />
          )}

          {volume && (
            <g className="cl-vol">
              {data.map((_, i) => {
                const h = (volAt(i) / volMax) * volArea;
                return (
                  <rect
                    key={i}
                    x={(X(i) - bw / 2).toFixed(1)}
                    y={(VBH - h).toFixed(1)}
                    width={bw.toFixed(1)}
                    height={h.toFixed(1)}
                    rx="1"
                  />
                );
              })}
            </g>
          )}

          {area && <path className="cl-area" d={areaPath} fill={`url(#g${gid})`} />}

          {pastPath && (
            <path
              className="cl-line"
              d={pastPath}
              vectorEffect="non-scaling-stroke"
            />
          )}
          <path
            className={`cl-line now${draw ? " cl-draw" : ""}`}
            d={nowPath}
            style={{ stroke: nowColor }}
            vectorEffect="non-scaling-stroke"
          />

          {crosshair && hi != null && Number.isFinite(sx) && Number.isFinite(sy) && (
            <>
              <line
                className="cl-cross"
                x1={sx}
                x2={sx}
                y1={top - 4}
                y2={bottom + 6}
                vectorEffect="non-scaling-stroke"
              />
              <circle
                className="cl-dot"
                cx={sx}
                cy={sy}
                r="4"
                fill={nowColor}
                vectorEffect="non-scaling-stroke"
              />
            </>
          )}

          {/* end marker dot */}
          {(!crosshair || hi == null) && Number.isFinite(Y(data[n - 1])) ? (
            <circle
              cx={X(n - 1)}
              cy={Y(data[n - 1])}
              r="4"
              fill={nowColor}
              vectorEffect="non-scaling-stroke"
            />
          ) : null}
        </svg>

        {baseline && (
          <div className="cl-close" style={{ top: `${(Y(data[0]) / VBH) * 100}%` }}>
            Previous close {money(data[0])}
          </div>
        )}
        {pctEnd != null && (
          <div
            className={`cl-pct ${pctEnd >= 0 ? "pos" : "neg"}`}
            style={{ top: `${(Y(data[n - 1]) / VBH) * 100}%` }}
          >
            {pctEnd >= 0 ? "+" : "−"}
            {Math.abs(pctEnd).toFixed(2)}%
          </div>
        )}
        {tip}
      </div>
      {axis && (
        <div className="cl-axis">
          {axis.map((a) => (
            <span key={a}>{a}</span>
          ))}
        </div>
      )}
    </>
  );
}

/* Compact sparkline for table rows / bot rows. */
export function Sparkline({
  data,
  width = 130,
  height = 30,
  tone = "gain",
}: {
  data: number[];
  width?: number;
  height?: number;
  tone?: "gain" | "loss";
}) {
  const n = data.length;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const span = max - min || 1;
  const X = (i: number) => (i / (n - 1)) * width;
  const Y = (v: number) => height - 3 - ((v - min) / span) * (height - 6);
  let d = `M${X(0).toFixed(1)},${Y(data[0]).toFixed(1)}`;
  for (let i = 1; i < n; i++) d += ` L${X(i).toFixed(1)},${Y(data[i]).toFixed(1)}`;
  return (
    <span className="spark" style={{ width, height }}>
      <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
        <path
          d={d}
          style={{ stroke: tone === "gain" ? "var(--gain)" : "var(--loss)" }}
          vectorEffect="non-scaling-stroke"
        />
      </svg>
    </span>
  );
}
