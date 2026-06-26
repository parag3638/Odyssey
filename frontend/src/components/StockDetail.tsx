"use client";

import { useState } from "react";
import { logoColor, pct, splitMoney } from "@/lib/format";
import type { KpiItem } from "@/lib/types";
import {
  Button,
  IconButton,
  KpiStrip,
  Ranges,
  Tabs,
  TickerLogo,
} from "@/components/ui";
import { LineChart } from "@/components/ui/LineChart";
import {
  BookmarkIcon,
  ColumnsIcon,
  ExpandIcon,
  SparklesIcon,
} from "@/components/icons";

const RANGES = ["1D", "1W", "1M", "3M", "YTD", "1Y", "All"];

/* Stock detail view — reference #5. */
export function StockDetail({
  symbol,
  name,
  color,
  price,
  prevClose,
  series,
  kpis,
  newsText,
  newsTime = "Summarized 6:00 AM · 2 days ago",
}: {
  symbol: string;
  name: string;
  color?: string;
  price: number;
  prevClose: number;
  series: number[];
  kpis: KpiItem[];
  newsText: string;
  newsTime?: string;
}) {
  const [range, setRange] = useState("1D");
  const [tab, setTab] = useState("news");
  const { whole, cents } = splitMoney(price);
  const change = price - prevClose;
  const changePct = (change / prevClose) * 100;
  const up = change >= 0;
  const c = color ?? logoColor(symbol);

  return (
    <div>
      <div className="d-head">
        <TickerLogo symbol={symbol} color={c} square />
        <div>
          <div className="tk">
            {symbol}
          </div>
          <div className="nm3">{name}</div>
        </div>
        <span className="sp" />
        <Button variant="ghost" sm>
          <SparklesIcon />
          Analyze
        </Button>
        <IconButton aria-label="Chart options">
          <ColumnsIcon />
        </IconButton>
        <IconButton aria-label="Save">
          <BookmarkIcon />
        </IconButton>
      </div>

      <div className="d-grid">
        <div>
          <div className="prices">
            <div>
              <div className="lbl">At close</div>
              <div>
                <span className="pr tnum">
                  ${whole}
                  <span className="dec">.{cents}</span>
                </span>
                <span className={`ch ${up ? "pos" : "neg"}`}>
                  {up ? "+" : "−"}
                  {Math.abs(change).toFixed(2)} ({pct(changePct)})
                </span>
              </div>
            </div>
            <span className="exch">USD · Nasdaq</span>
          </div>

          <div style={{ marginTop: 16 }}>
            <LineChart
              data={series}
              height={300}
              tone={up ? "gain" : "loss"}
              area
              volume
              baseline
              grid
              crosshair
              hover="tooltip"
              dates={series.map(
                (_, i) => `Jun ${5 + (i % 20)} · ${9 + (i % 6)}:0${i % 6} AM`,
              )}
            />
          </div>
          <div style={{ marginTop: 14 }}>
            <Ranges options={RANGES} value={range} onChange={setRange} />
          </div>

          <div style={{ marginTop: 22 }}>
            <KpiStrip items={kpis} />
          </div>
        </div>

        <div>
          <div className="rtabs">
            <Tabs
              options={[
                { label: "News", value: "news" },
                { label: "KPIs", value: "kpis" },
                { label: "Earnings", value: "earnings" },
                { label: "About", value: "about" },
              ]}
              value={tab}
              onChange={setTab}
            />
          </div>
          <div className="newssum">
            <div className="nsh">
              News summary
              <IconButton aria-label="Expand">
                <ExpandIcon />
              </IconButton>
            </div>
            <div className="nstext">{newsText}</div>
            <div className="nsfoot">
              <span className="pill r">2 days ago</span>
              {newsTime}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
