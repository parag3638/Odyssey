import { pct, signedMoney, signClass, splitMoney } from "@/lib/format";
import { EyeIcon, EyeOffIcon, ArrowRightIcon } from "@/components/icons";
import { Ranges, Skeleton } from "@/components/ui";
import { LineChart } from "@/components/ui/LineChart";

export const HERO_RANGES = ["1D", "1W", "1M", "3M", "6M", "YTD", "1Y", "ALL"];

/* Build illustrative time/date labels for the chart crosshair. */
function labelsFor(range: string, n: number): string[] {
  const times = ["9:31 AM", "10:20 AM", "11:15 AM", "12:40 PM", "1:30 PM", "2:25 PM", "3:10 PM", "3:58 PM"];
  const days = ["Mon", "Tue", "Wed", "Thu", "Fri"];
  return Array.from({ length: n }, (_, i) =>
    range === "1D"
      ? `Today · ${times[i % times.length]} ET`
      : `${days[i % days.length]} · ${times[i % times.length]} ET`,
  );
}

export function PortfolioHero({
  balance,
  masked,
  onToggleMask,
  todayAmount,
  todayPct,
  range,
  onRange,
  series,
  hasData,
  loading,
}: {
  balance: number;
  masked: boolean;
  onToggleMask: () => void;
  todayAmount: number;
  todayPct: number;
  range: string;
  onRange: (r: string) => void;
  series: number[];
  hasData: boolean;
  loading?: boolean;
}) {
  const { sign, whole, cents } = splitMoney(balance);
  const tone = todayPct >= 0 ? "gain" : "loss";

  return (
    <section>
      <div className="balrow">
        <div className="balbig tnum">
          {loading ? (
            <Skeleton w={240} h={44} r={10} />
          ) : !hasData ? (
            <span className="faint">$0.00</span>
          ) : masked ? (
            "$•••••••"
          ) : (
            <>
              {sign}${whole}
              <span className="dec">.{cents}</span>
            </>
          )}
        </div>
        <button
          type="button"
          className="eyeb"
          onClick={onToggleMask}
          title={masked ? "Show balance" : "Hide balance"}
          aria-label={masked ? "Show balance" : "Hide balance"}
        >
          {masked ? <EyeOffIcon /> : <EyeIcon />}
        </button>
      </div>

      <div className="pastday">
        {loading ? (
          <Skeleton w={170} h={14} />
        ) : hasData ? (
          <>
            <span className={signClass(todayAmount)}>
              {signedMoney(todayAmount)} ({pct(todayPct)})
            </span>
            today
            <ArrowRightIcon />
          </>
        ) : (
          <span className="faint">No holdings yet</span>
        )}
      </div>

      <div className="chartbox" style={{ height: 340, marginTop: 22 }}>
        {loading ? (
          // TradingView-style loading: a shimmer placeholder over the chart area,
          // sized to the real chart so there's no layout shift when data lands.
          <Skeleton h={340} r={16} />
        ) : hasData ? (
          <LineChart
            data={series}
            height={340}
            tone={tone}
            area
            crosshair
            hover="date"
            draw
            dates={labelsFor(range, series.length)}
            ariaLabel="Portfolio value over time (illustrative)"
          />
        ) : (
          // No holdings yet → don't fake a line; show a quiet placeholder.
          <div className="chart-empty">
            <span className="faint">Your portfolio chart appears once you hold positions.</span>
          </div>
        )}
      </div>
      <div className="herodiv" />
      <Ranges options={HERO_RANGES} value={range} onChange={onRange} hero />
    </section>
  );
}
