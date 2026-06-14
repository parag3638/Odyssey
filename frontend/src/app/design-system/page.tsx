"use client";

import { useState } from "react";
import {
  AccountRow,
  AssetBreakdown,
  Badge,
  Button,
  Card,
  Donut,
  EarningsCalendar,
  EmptyState,
  IconButton,
  IconTile,
  KpiStrip,
  NewsCard,
  Pill,
  PromoCarousel,
  Ranges,
  ReturnBadge,
  SearchBar,
  SegmentedControl,
  Skeleton,
  Stat,
  StatGrid,
  Switch,
  Tabs,
  Tag,
  TickerLogo,
} from "@/components/ui";
import { LineChart, Sparkline } from "@/components/ui/LineChart";
import { HoldingsTable } from "@/components/HoldingsTable";
import { ScreenerTable } from "@/components/ScreenerTable";
import { StockDetail } from "@/components/StockDetail";
import {
  BriefcaseIcon,
  SailIcon,
  LightningIcon,
  MoonIcon,
  PlusIcon,
  PositionsIcon,
  SparklesIcon,
  SunIcon,
} from "@/components/icons";
import {
  makeSeries,
  sampleAccounts,
  sampleAllocation,
  sampleDetailSeries,
  sampleEarnings,
  sampleHeroSeries,
  sampleHoldings,
  sampleKpis,
  sampleNews,
  samplePerfSeries,
  sampleScreener,
  sampleStats,
} from "@/lib/sample";

const TOKENS = [
  { n: "--bg", v: "var(--bg)" },
  { n: "--panel", v: "var(--panel)" },
  { n: "--card", v: "var(--card)" },
  { n: "--card-2", v: "var(--card-2)" },
  { n: "--card-3", v: "var(--card-3)" },
  { n: "--line", v: "var(--line)" },
  { n: "--text", v: "var(--text)" },
  { n: "--text-2", v: "var(--text-2)" },
  { n: "--text-3", v: "var(--text-3)" },
  { n: "--gain", v: "var(--gain)" },
  { n: "--loss", v: "var(--loss)" },
  { n: "--tint", v: "var(--gain-bg)" },
];

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <>
      <div className="sg-sec">{title}</div>
      {children}
    </>
  );
}

export default function DesignSystemPage() {
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [seg, setSeg] = useState("value");
  const [tab, setTab] = useState("news");
  const [range, setRange] = useState("1M");
  const [sw, setSw] = useState(true);

  const toggleTheme = () => {
    const next = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    setTheme(next);
  };

  const holdingsTotal = sampleHoldings.reduce(
    (s, h) => s + h.qty * (h.price ?? h.avgCost),
    0,
  );

  return (
    <div className="wrap wide">
      <div className="sg-head">
        <span className="flame">
          <SailIcon />
        </span>
        <span className="sg-title">Odyssey Design System</span>
        <span style={{ flex: 1 }} />
        <Button variant="ghost" sm onClick={toggleTheme}>
          {theme === "dark" ? <SunIcon /> : <MoonIcon />}
          {theme === "dark" ? "Light" : "Dark"}
        </Button>
      </div>
      <p className="sg-lead">
        The dark fintech component library behind Odyssey — matching the Wealthsimple /
        Fey references. Every component below is live and themable. See
        <code> design/DESIGN_SYSTEM.md</code> for usage and the reference images.
      </p>

      <Section title="Color tokens">
        <div className="sg-grid">
          {TOKENS.map((t) => (
            <div className="sg-swatch" key={t.n}>
              <div className="sw" style={{ background: t.v }} />
              <div className="sl">
                <b>{t.n}</b>
              </div>
            </div>
          ))}
        </div>
      </Section>

      <Section title="Typography & numerics">
        <Card pad>
          <div className="balbig tnum" style={{ marginBottom: 16 }}>
            $139,980<span className="dec">.16</span>
          </div>
          <div className="prices">
            <div>
              <div className="lbl">At close</div>
              <span className="pr tnum">
                $330<span className="dec">.55</span>
              </span>
              <span className="ch pos">+5.85 (+1.80%)</span>
            </div>
          </div>
          <p className="muted" style={{ marginTop: 14 }}>
            Geist · tabular figures · dimmed cents — the signature price treatment.
          </p>
        </Card>
      </Section>

      <Section title="Primitives">
        <div className="sg-row">
          <Pill tone="g">+1.80%</Pill>
          <Pill tone="r">−0.20%</Pill>
          <Pill tone="o-g">Buy</Pill>
          <Pill tone="o-r">Sell</Pill>
          <Pill tone="n">Pending</Pill>
          <Tag dot>Trailing</Tag>
          <Tag muted>Liquid fund</Tag>
          <Badge tone="g">active</Badge>
          <Badge tone="n">paused</Badge>
          <ReturnBadge amount={133.5} percent={4.2} />
          <ReturnBadge amount={-1.4} percent={-0.2} />
        </div>
        <div className="sg-row" style={{ marginTop: 16 }}>
          <TickerLogo symbol="TSLA" />
          <TickerLogo symbol="NVDA" />
          <TickerLogo symbol="AAPL" square />
          <IconTile color="var(--tile-red)">
            <LightningIcon />
          </IconTile>
          <IconTile color="var(--tile-violet)">
            <BriefcaseIcon />
          </IconTile>
          <span className="twoparts">
            11.89% <Donut percent={11.89} />
          </span>
          <Switch on={sw} onToggle={() => setSw((s) => !s)} label="Demo" />
          <Skeleton w={120} h={14} />
        </div>
      </Section>

      <Section title="Buttons & controls">
        <div className="sg-row">
          <Button variant="primary">Primary</Button>
          <Button>Default</Button>
          <Button variant="ghost">Ghost</Button>
          <Button variant="buy">Buy</Button>
          <Button variant="sell">Sell</Button>
          <IconButton aria-label="Add">
            <PlusIcon />
          </IconButton>
          <IconButton round aria-label="Spark">
            <SparklesIcon />
          </IconButton>
        </div>
        <div className="sg-row" style={{ marginTop: 16 }}>
          <SegmentedControl
            options={[
              { label: "Account value", value: "value" },
              { label: "Performance", value: "perf" },
            ]}
            value={seg}
            onChange={setSeg}
          />
          <Tabs
            options={["news", "kpis", "earnings", "about"]}
            value={tab}
            onChange={setTab}
          />
          <Ranges
            options={["1D", "1W", "1M", "3M", "YTD", "All"]}
            value={range}
            onChange={setRange}
          />
          <SearchBar />
        </div>
      </Section>

      <Section title="Charts (custom SVG)">
        <div className="sg-grid">
          <Card pad>
            <div className="sg-label">Hero — area + crosshair</div>
            <LineChart data={sampleHeroSeries} height={160} tone="gain" area crosshair hover="date" />
          </Card>
          <Card pad>
            <div className="sg-label">Performance — split past/now</div>
            <LineChart
              data={samplePerfSeries}
              height={160}
              splitAt={Math.floor(samplePerfSeries.length / 2)}
              tone="loss"
              area
              pctEnd={-4.12}
              axis={["Nov 28", "Dec 12", "Dec 26", "Jan 2"]}
            />
          </Card>
          <Card pad>
            <div className="sg-label">Detail — volume + tooltip + baseline</div>
            <LineChart data={sampleDetailSeries} height={160} tone="gain" area volume baseline grid crosshair />
          </Card>
          <Card pad>
            <div className="sg-label">Sparklines</div>
            <div className="sg-row">
              <Sparkline data={makeSeries(28, 20, 8, 1)} tone="gain" />
              <Sparkline data={makeSeries(28, 20, -8, 4)} tone="loss" />
            </div>
          </Card>
        </div>
      </Section>

      <Section title="Holdings table (sortable · filter · column customizer)">
        <HoldingsTable holdings={sampleHoldings} totalValue={holdingsTotal} />
      </Section>

      <Section title="Stock screener (natural-language)">
        <ScreenerTable rows={sampleScreener} />
      </Section>

      <Section title="KPI strip">
        <KpiStrip items={sampleKpis} />
      </Section>

      <Section title="Stat grid · breakdown · account rows">
        <Card pad style={{ marginBottom: 16 }}>
          <StatGrid>
            {sampleStats.map((s) => (
              <Stat key={s.pk} value={s.pv} label={s.pk} tone={s.cls} hl={s.hl} />
            ))}
          </StatGrid>
        </Card>
        <div className="sg-grid">
          <AssetBreakdown title="Asset breakdown" slices={sampleAllocation} />
          <div className="acctlist">
            {sampleAccounts.map((a) => (
              <AccountRow key={a.name} item={a} />
            ))}
          </div>
        </div>
      </Section>

      <Section title="News card">
        <div style={{ maxWidth: 720 }}>
          <NewsCard item={sampleNews} />
        </div>
      </Section>

      <Section title="Earnings calendar">
        <Card pad>
          <EarningsCalendar days={sampleEarnings} />
        </Card>
      </Section>

      <Section title="Stock detail">
        <StockDetail
          symbol="NVDA"
          name="NVIDIA Corporation"
          color="#76b900"
          price={179.52}
          prevClose={177.74}
          series={sampleDetailSeries}
          kpis={sampleKpis}
          newsText="Nvidia's AI-driven revenue surge has nearly tripled its stock value since June 2023, despite a recent 12% pullback. Analyst debates and a premium valuation spotlight its role in powering the AI boom."
        />
      </Section>

      <Section title="Promo carousel & empty state">
        <div className="sg-grid">
          <PromoCarousel
            slides={[
              { title: "Get up to a 3% match", desc: "Earn a match on transfers and 5,000+ entries in the giveaway." },
              { title: "Automate a trailing stop", desc: "Let a bot lock in gains with a moving floor." },
            ]}
          />
          <Card>
            <EmptyState
              icon={<PositionsIcon />}
              title="No watchlist yet"
              desc="Symbols you follow will appear here."
              action={<Button sm><PlusIcon /> Add symbol</Button>}
            />
          </Card>
        </div>
      </Section>

      <div className="foot" style={{ marginTop: 48 }}>
        <b>Odyssey</b> Design System · all sample data · matches design/DESIGN_SYSTEM.md
      </div>
    </div>
  );
}
