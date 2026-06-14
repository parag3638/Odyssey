import { money, pct } from "@/lib/format";
import type { NewsItem } from "@/lib/types";
import { TickerLogo } from "./primitives";
import { LineChart } from "./LineChart";
import { SailIcon } from "@/components/icons";

/* The TSLA news card — reference #10. */
export function NewsCard({ item }: { item: NewsItem }) {
  const down = (item.perfPct ?? 0) < 0;
  return (
    <div className="newscard">
      <div className="nh">
        <span className="chip">
          <TickerLogo symbol={item.symbol} color={item.color} size="sm" />
          {item.symbol}
        </span>
        <span className="when">
          <b>{item.time.split("·")[0]?.trim()}</b>
          {item.time.includes("·") ? ` · ${item.time.split("·")[1].trim()}` : ""}
        </span>
      </div>

      <p className="ntext">{item.text}</p>

      {item.series && (
        <div className="nperf">
          <div className="nptop">
            Performance since last transaction
            {item.side && (
              <span className={`pill ${item.side === "sell" ? "o-r" : "o-g"}`}>
                {item.side === "sell" ? "Sell" : "Buy"}
              </span>
            )}
          </div>
          <div className="npbody">
            <div className="npmeta">
              <div className="npk">{item.perfLabel}</div>
              <div className="npv">
                {item.perfValue != null && money(item.perfValue)}
                {item.perfPct != null && (
                  <span className={`pill ${down ? "r" : "g"}`}>
                    {pct(item.perfPct)}
                  </span>
                )}
              </div>
            </div>
            <div className="npchart">
              <LineChart
                data={item.series}
                height={92}
                splitAt={item.splitAt}
                tone={down ? "loss" : "gain"}
                area
              />
            </div>
          </div>
        </div>
      )}

      <div className="nfoot">
        <span className="flame" style={{ width: 18, height: 18, borderRadius: 5 }}>
          <SailIcon />
        </span>
        {item.account} · {item.date}
      </div>
    </div>
  );
}
