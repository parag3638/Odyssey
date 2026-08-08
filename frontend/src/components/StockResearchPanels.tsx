"use client";

import { useState } from "react";
import { Pill, Skeleton } from "@/components/ui";
import type {
  DividendPoint,
  EarningsPoint,
  NewsArticle,
  RecommendationPoint,
} from "@/lib/api";

/* Shared News/Earnings/Dividends/Analysis panels — used by both the per-stock
   detail page (/stocks/[symbol]) and the Research hub (/research/[symbol]) so
   the two stay visually and behaviorally identical without duplicating markup. */

function timeAgo(iso: string | null): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 3600) return `${Math.max(1, Math.floor(s / 60))}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  const d = Math.floor(s / 86400);
  if (d < 30) return `${d}d ago`;
  return new Date(t).toLocaleDateString();
}

function NewsThumb({ src }: { src: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) return null;
  // Many providers (e.g. Benzinga) ship a tiny `?width=20` preview — request a
  // retina-crisp size for the 64px thumbnail when the param is present.
  const hi = src.replace(/([?&]width=)\d+/, "$1160");
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img className="nr-thumb" src={hi} alt="" loading="lazy" onError={() => setFailed(true)} />
  );
}

export function NewsTab({ items, loading }: { items: NewsArticle[]; loading: boolean }) {
  if (loading) {
    return (
      <div className="newslist" aria-busy="true">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="newsrow is-skel">
            <div className="nr-body">
              <Skeleton w="94%" h={13} />
              <Skeleton w="60%" h={13} style={{ marginTop: 8 }} />
              <Skeleton w={104} h={10} style={{ marginTop: 12 }} />
            </div>
            {i % 2 === 0 && <Skeleton w={64} h={64} r={12} />}
          </div>
        ))}
      </div>
    );
  }
  if (items.length === 0)
    return <div className="faint" style={{ fontSize: 13 }}>No recent news.</div>;
  return (
    <div className="newslist">
      {items.slice(0, 20).map((a, i) => (
        <a key={i} className="newsrow" href={a.url} target="_blank" rel="noopener noreferrer">
          <div className="nr-body">
            <div className="nr-title">{a.headline}</div>
            <div className="nr-meta">
              <span className="nr-src">{a.source}</span>
              {a.datetime && (
                <>
                  <span className="nr-dot" />
                  <span>{timeAgo(a.datetime)}</span>
                </>
              )}
            </div>
          </div>
          {a.image && <NewsThumb src={a.image} />}
        </a>
      ))}
    </div>
  );
}

export function EarningsTab({ items }: { items: EarningsPoint[] }) {
  if (items.length === 0)
    return <div className="faint" style={{ fontSize: 13 }}>No earnings data (add a Finnhub key).</div>;
  return (
    <div className="botstat">
      {items.slice(0, 6).map((e, i) => {
        const beat = e.actual != null && e.estimate != null && e.actual >= e.estimate;
        return (
          <div className="line" key={i}>
            <span className="k">{e.period}</span>
            <span className="v">
              <span className="tnum">{e.actual != null ? `$${e.actual.toFixed(2)}` : "—"}</span>{" "}
              {e.actual != null && e.estimate != null && (
                <Pill tone={beat ? "g" : "r"}>{beat ? "Beat" : "Miss"}</Pill>
              )}
            </span>
          </div>
        );
      })}
    </div>
  );
}

export function DividendsTab({ items }: { items: DividendPoint[] }) {
  if (items.length === 0)
    return <div className="faint" style={{ fontSize: 13 }}>No recent dividends.</div>;
  return (
    <div className="botstat">
      {items.slice(0, 8).map((d, i) => (
        <div className="line" key={i}>
          <span className="k">{d.ex_date}</span>
          <span className="v tnum">${d.amount.toFixed(2)}</span>
        </div>
      ))}
    </div>
  );
}

export function AnalysisTab({ items }: { items: RecommendationPoint[] }) {
  const latest = items[0];
  if (!latest)
    return <div className="faint" style={{ fontSize: 13 }}>No analyst data (add a Finnhub key).</div>;
  const rows = [
    { k: "Strong buy", v: latest.strongBuy ?? 0, tone: "g" as const },
    { k: "Buy", v: latest.buy ?? 0, tone: "g" as const },
    { k: "Hold", v: latest.hold ?? 0, tone: "n" as const },
    { k: "Sell", v: latest.sell ?? 0, tone: "r" as const },
    { k: "Strong sell", v: latest.strongSell ?? 0, tone: "r" as const },
  ];
  const total = rows.reduce((s, r) => s + r.v, 0) || 1;
  return (
    <div>
      <div className="faint" style={{ fontSize: 11.5, marginBottom: 12 }}>
        Analyst ratings · {latest.period}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {rows.map((r) => (
          <div key={r.k} style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 12.5 }}>
            <span className="muted" style={{ width: 78 }}>{r.k}</span>
            <span className="allocbar" style={{ flex: 1, height: 8 }}>
              <span
                style={{
                  flexGrow: r.v / total,
                  background: r.tone === "g" ? "var(--gain)" : r.tone === "r" ? "var(--loss)" : "var(--text-3)",
                }}
              />
              <span style={{ flexGrow: 1 - r.v / total, background: "var(--card-3)" }} />
            </span>
            <span className="tnum" style={{ width: 22, textAlign: "right" }}>{r.v}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
