import type { StockRow } from "@/lib/api";
import { pct, signClass, signedMoney, splitMoney } from "@/lib/format";
import { ArrowRightIcon } from "@/components/icons";
import { Ranges, Select, Skeleton, TickerLogo } from "@/components/ui";
import { LineChart } from "@/components/ui/LineChart";

/* Backend-valid history buckets (see backend market_data._range). No "6M". */
export const STOCK_HERO_RANGES = ["1D", "1W", "1M", "3M", "YTD", "1Y", "ALL"];

/** Dashboard hero: a single-stock chart picked from a quiet inline dropdown,
 *  with the price block + chart below. */
export function StockHero({
  row,
  options,
  selected,
  onSelect,
  range,
  onRange,
  series,
  dates,
  realChart,
  loadingChart,
}: {
  row: StockRow | null;
  options: { label: string; value: string }[];
  selected: string;
  onSelect: (s: string) => void;
  range: string;
  onRange: (r: string) => void;
  series: number[];
  dates?: string[];
  realChart: boolean;
  loadingChart: boolean;
}) {
  const { sign, whole, cents } = splitMoney(row?.price ?? 0);
  const change = row?.change ?? 0;
  const changePct = row?.change_pct ?? 0;
  const tone = changePct >= 0 ? "gain" : "loss";

  return (
    <section>
      <div className="d-head">
        {selected ? (
          <TickerLogo symbol={selected} logo={row?.logo_url} square />
        ) : (
          <Skeleton w={42} h={42} r={12} />
        )}
        <Select
          value={selected}
          options={options}
          onChange={onSelect}
          ariaLabel="Select a stock"
          placeholder="Select a stock"
          className="herosel"
          disabled={!options.length}
        />
        <span className="sp" />
      </div>

      <div className="balrow" style={{ marginTop: 14 }}>
        <div className="balbig tnum">
          {row ? (
            <>
              {sign}${whole}
              <span className="dec">.{cents}</span>
            </>
          ) : (
            <Skeleton w={240} h={44} r={10} />
          )}
        </div>
      </div>

      <div className="pastday">
        {row ? (
          <>
            <span className={signClass(change)}>
              {signedMoney(change)} ({pct(changePct)})
            </span>
            today
            <ArrowRightIcon />
          </>
        ) : (
          <Skeleton w={170} h={14} />
        )}
      </div>

      {row && !loadingChart && !realChart && (
        <div className="faint" style={{ fontSize: 12, marginTop: 10 }}>
          Price history unavailable — showing an illustrative line.
        </div>
      )}

      <div className="chartbox" style={{ height: 340, marginTop: 16 }}>
        {loadingChart || !row ? (
          <Skeleton h={340} r={16} />
        ) : (
          <LineChart
            data={series}
            height={340}
            tone={tone}
            area
            crosshair
            hover="date"
            draw
            dates={dates}
            ariaLabel={`${selected} price over time`}
          />
        )}
      </div>

      <div className="herodiv" />
      <Ranges options={STOCK_HERO_RANGES} value={range} onChange={onRange} hero />
    </section>
  );
}
