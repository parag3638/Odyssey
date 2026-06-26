# Odyssey Design System

The dark fintech design language for Odyssey — a paper-trading dashboard. Near-monochrome
layered surfaces, calm whitespace, emerald gains / coral losses, and a coherent dark/light
system.

> **This file is the source of truth for UI work.** Before building or changing any
> screen/component, read this doc, skim the screen catalog, and reuse the existing
> tokens (`frontend/src/app/styles/tokens.css`) and component library
> (`frontend/src/components/ui/`). A live, browsable styleguide of every component is at
> the **`/design-system`** route (`npm run dev` → http://localhost:3000/design-system).

---

## The aesthetic in one breath

Near-black layered surfaces · hairline borders · an emerald top-glow · calm whitespace ·
**tabular** figures with **dimmed cents** · emerald gains / coral losses · thin split-color
charts with crosshair tooltips · dense-but-airy tables with logos, allocation donuts, and
tinted return pills · segmented pills for timeframes · a left icon rail (desktop) that
reflows to a top bar (mobile). Dark is default; light is a first-class peer.

Achieved exemplars (screenshots of this codebase):
`reference/achieved/styleguide-dark-full.png`, `holdings-table.png`, `charts.png`,
`news-card.png`, `stock-detail.png`, `light-theme.png`, `overview-shell.png`.

---

## Screen catalog

The screens that make up the app and the key elements each one needs. A live styleguide of
every component is at the `/design-system` route; realized screenshots live in
`reference/achieved/`.

| # | Screen | Key elements |
|---|--------|--------------|
| 1 | Move / action cards | Big action-card grid (`.actioncard`), FAQ list, dark elevated tiles |
| 2 | Household | Balance + line chart, account rows (`.acctrow`), **asset/household breakdown** stacked bar + legend (`AllocationBar`/`AssetBreakdown`) |
| 3 | Home / Overview | Balance hero, timeframe pills, right rail (quick actions, promo carousel, Holdings widget) |
| 4 | Earnings | Flip-card date tiles, highlighted "today", logo clusters, "N events" (`EarningsCalendar`) |
| 5 | Stock detail | Dim-cents price, line+**volume**+crosshair tooltip, **KPI strip**, News/KPIs/Earnings/About tabs (`StockDetail`) |
| 6 | Light vs dark | Both themes side by side; colored account **icon tiles** (`IconTile`) — light/dark parity |
| 7 | Column customizer | Column visibility + drag-reorder popover (`ColumnCustomizer`); donut allocation indicator |
| 8 | Holdings table | **The hallmark table**: logo, account pills, allocation donut, qty, price, signed return pills, value, all-time return, sortable headers (`HoldingsTable`/`DataTable`) |
| 9 | Stock finder | NL query bar, sector pills, dense sortable screener, active-column highlight (`ScreenerTable`) |
| 10 | News card | Ticker chip + time, headline, embedded split perf-chart, Sell/Buy pill, account·date footer (`NewsCard`) |
| 11 | Holdings detail | Stat cards row (`StatGrid`), big area chart with date axis, Account value/Performance toggle |

---

## Tokens

Defined in `frontend/src/app/styles/tokens.css`; exposed to Tailwind v4 via `@theme inline`
in `globals.css`. Themed by `data-theme="dark|light"` on `<html>`.

### Color (dark · light)
| Token | Dark | Light | Use |
|-------|------|-------|-----|
| `--bg` | `#060607` | `#f3f3f1` | app background |
| `--panel` | `#0b0b0d` | `#ffffff` | nav/overlay |
| `--card` / `--card-2` / `--card-3` | `#0f0f12` / `#15151a` / `#1c1c22` | `#ffffff` / `#f0efec` / `#e8e7e3` | elevation layers |
| `--line` / `--line-2` | white 7% / 4.5% | black 8.5% / 5% | hairlines |
| `--text` / `--text-2` / `--text-3` | `#f4f4f6` / `#8b8b92` / `#56565d` | `#101012` / `#6c6c72` / `#9a9aa0` | text ramp 100/60/30 |
| `--gain` / `--loss` | `#34d399` / `#f0616b` | `#1f9d57` / `#df564f` | up / down |
| `--tint` | emerald 5% | — | top-glow |
| `--shadow` | deep | soft | card depth |

Each semantic also has `-bg` and `-bd` tints (e.g. `--gain-bg`, `--gain-bd`) for pills.

### Scale (theme-independent)
- **Radius:** `--r-1:8` `--r-2:11` `--r-3:14` `--r-4:18` `--r-pill:999`
- **Motion:** `--ease: cubic-bezier(.2,.7,.2,1)` · `--ease-out` · `--dur-1:.12s` `--dur-2:.18s` `--dur-3:.35s`
- **Icon-tile hues:** `--tile-red/amber/violet/blue/green/teal`
- **Focus:** `--ring` (emerald, used by `:focus-visible`)

---

## Typography

- **Family:** Geist (sans) + Geist Mono — clean neutral grotesk that matches the references.
- **Figures:** always `.tnum` (tabular) for money/percent so columns align.
- **Signature price treatment:** bright integer, **dimmed cents** via `.dec` — `$179`<span style="opacity:.5">`.52`</span>. Use `splitMoney()` from `lib/format.ts`.
- **Scale:** balance 46px/600 · price 28px/600 · section 15–17px/600 · body 13.5–14px · labels 11–12.5px/`--text-3`.
- Negatives use a real minus sign `−` (U+2212), not a hyphen (handled by `lib/format.ts`).

---

## Motion & accessibility

- **Page load:** stagger with `.reveal` + `--i` index (60ms cascade).
- **Charts:** `draw` animation, smooth crosshair, non-scaling strokes.
- **Hover/active:** `var(--dur-1/2)` transitions on rows, tiles, controls.
- **Reduced motion:** all animation/transition collapsed via `prefers-reduced-motion`.
- **Focus:** visible emerald ring on keyboard focus only (`:focus-visible`).
- **Contrast:** dark text ramp meets WCAG AA on `--bg`; gain/loss chosen for legibility on dark.

---

## Component catalog

All components live in `frontend/src/components/ui/` (barrel: `ui/index.ts`) and apply the
global semantic classes documented here. Feature compositions live in `components/`.

### Primitives (`ui/primitives.tsx`)
`Card` · `Pill` (`g`/`r`/`n`/`o-g`/`o-r`) · `Tag` (dot/muted) · `Badge` · `ReturnBadge`
(signed money + % pill) · `TickerLogo` (letter-badge, `square`/`sm`) · `IconTile` (colored
account tile) · `Donut` (allocation indicator) · `Switch` · `Skeleton` · `EmptyState` ·
`Stat`/`StatGrid`.

### Controls (`ui/controls.tsx`)
`Button` (`primary`/`buy`/`sell`/`ghost`, `sm`) · `IconButton` (`round`) ·
`SegmentedControl` · `Tabs` (`ghost` underline) · `Ranges` (timeframe pills, `hero`) ·
`SearchBar` (with `/` hint).

### Data
`DataTable` (`ui/DataTable.tsx`) — column-driven, sortable headers with active-column
highlight, row hover/active, sticky-friendly, skeleton + empty states. `ColumnCustomizer`
(`ui/ColumnCustomizer.tsx`) — visibility toggles + drag reorder popover.

### Charts (`ui/LineChart.tsx`) — custom SVG, zero deps
`LineChart` props: `data`, `height`, `splitAt` (past→now split), `tone`
(`gain`/`loss`/`auto`/`neutral`), `area`, `volume`, `baseline` (previous-close line),
`grid`, `crosshair`, `hover` (`tooltip`/`date`), `pctEnd`, `axis`, `draw`, `dates`.
`Sparkline` for table/bot rows. Strokes use `vector-effect: non-scaling-stroke`.

### Widgets & compositions
`KpiStrip` · `AllocationBar` / `AssetBreakdown` · `AccountRow` · `NewsCard` ·
`EarningsCalendar` · `PromoCarousel` · `PortfolioHero` · `HoldingsTable` · `ScreenerTable` ·
`StockDetail`.

---

## Data formatting (`frontend/src/lib/format.ts`)

`money` · `signedMoney` (+/−) · `splitMoney` (dim cents) · `pct` · `compact` ($1.05T/$97.6B/$250K)
· `signClass` (`pos`/`neg`) · `qtyFmt` · `initials` · `logoColor` (deterministic per symbol).
Returns: positive → `--gain` + `pill g`; negative → `--loss` + `pill r`. Account-type → `Tag`
with a dot. Sector → `Tag muted`.

---

## Adding a new feature and keeping it matching

1. **Reuse first.** Check `ui/` and the styleguide before writing markup. New screens are
   assembled from existing components 90% of the time.
2. **Tokens only.** Never hardcode colors/spacing — use the CSS variables. New colors get a
   token in `tokens.css` (and a light value).
3. **Numbers** use `.tnum` and `lib/format.ts` helpers (dim cents for prices, signed pills for returns).
4. **Surfaces:** `Card`/`.tcard`, radius `--r-3/--r-4`, `--shadow`, `--line` borders.
5. **Motion:** `.reveal` for entrance; `--dur-*`/`--ease`; honor reduced motion.
6. **Both themes:** verify dark *and* light (toggle on `/design-system`).
7. **A11y:** real `<button>`s, `aria-*`, keyboard focus, `:focus-visible`.
8. **Verify** against the reference gallery and the `/design-system` styleguide.

---

## File map

```
design/
  DESIGN_SYSTEM.md            ← this file
  odyssey-mockup.html         ← static mockup (chart algorithms, patterns)
  reference/
    images/                   ← target-screen reference slots (see README)
    achieved/                 ← screenshots of this codebase realizing the look
frontend/src/
  app/
    globals.css               ← imports + @theme
    styles/{tokens,base,components,charts}.css
    page.tsx                  ← Overview (revamped)
    design-system/page.tsx    ← living styleguide
  components/
    ui/                       ← the component library (barrel: index.ts)
    PortfolioHero, HoldingsTable, ScreenerTable, StockDetail, Nav, ActiveBots, …
  lib/{format,types,sample,api}.ts
```
