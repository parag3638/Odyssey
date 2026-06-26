// Generates frontend/public/snapshot.json — a static cache of the app's read
// data, used as a fallback when the backend is offline (demo mode).
// Run with the backend up:  node scripts/gen-snapshot.mjs
import { writeFileSync } from "node:fs";

const BASE = process.env.API_BASE || "http://localhost:8000";
const OUT = new URL("../public/snapshot.json", import.meta.url);

// KPI-relevant fundamentals keys (mirrors buildKpis) — keep the snapshot lean.
const KPI_KEYS = [
  "peTTM", "peBasicExclExtraTTM", "epsTTM", "epsBasicExclExtraItemsTTM",
  "dividendYieldIndicatedAnnual", "currentDividendYieldTTM", "marketCapitalization",
  "beta", "52WeekHigh", "52WeekLow", "grossMarginTTM",
];

async function get(path) {
  const r = await fetch(BASE + path);
  if (!r.ok) throw new Error(`${path} -> ${r.status}`);
  return r.json();
}
const tryGet = async (path, fallback) => {
  try {
    return await get(path);
  } catch {
    return fallback;
  }
};

async function mapPool(items, n, fn) {
  const out = new Array(items.length);
  let i = 0;
  await Promise.all(
    Array.from({ length: n }, async () => {
      while (i < items.length) {
        const idx = i++;
        out[idx] = await fn(items[idx], idx);
      }
    }),
  );
  return out;
}

const pick = (obj, keys) => {
  const o = {};
  if (obj) for (const k of keys) if (obj[k] !== undefined) o[k] = obj[k];
  return o;
};

console.log(`Snapshotting ${BASE} …`);

const [health, accounts, stocks, industries, movers, botsList, signals, activity] =
  await Promise.all([
    tryGet("/health", { status: "ok", mode: "paper" }),
    tryGet("/accounts", []),
    tryGet("/stocks?limit=500", []),
    tryGet("/stocks/industries", []),
    tryGet("/stocks/movers", { gainers: [], losers: [] }),
    tryGet("/bots", []),
    tryGet("/signals", []),
    tryGet("/activity?limit=200", []),
  ]);

const exact = {
  "/health": health,
  "/accounts": accounts,
  "/bots": botsList,
  "/stocks/industries": industries,
  "/stocks/movers": movers,
};

// portfolio for each account
for (const a of accounts) {
  exact[`/positions/${a.id}`] = await tryGet(`/positions/${a.id}`, []);
  exact[`/positions/${a.id}/quotes`] = await tryGet(`/positions/${a.id}/quotes`, []);
  exact[`/positions/${a.id}/summary`] = await tryGet(`/positions/${a.id}/summary`, { cash: null });
}

// bot detail map
const bots = {};
for (const b of botsList) bots[String(b.id)] = await tryGet(`/bots/${b.id}`, b);

// per-symbol fundamentals (metrics in batches of 60) + trimmed detail
const syms = stocks.map((s) => s.symbol);
const metrics = {};
for (let i = 0; i < syms.length; i += 60) {
  const batch = syms.slice(i, i + 60);
  Object.assign(metrics, await tryGet(`/stocks/metrics?symbols=${batch.join(",")}`, {}));
}

const detail = {};
await mapPool(syms, 8, async (s) => {
  const d = await tryGet(`/stocks/${s}`, null);
  if (d) detail[s] = { ...d, fundamentals: pick(d.fundamentals, KPI_KEYS) };
});

const snapshot = {
  generatedAt: new Date().toISOString(),
  exact,
  stocks,
  metrics,
  detail,
  bots,
  signals,
  activity,
};

writeFileSync(OUT, JSON.stringify(snapshot));
const bytes = JSON.stringify(snapshot).length;
console.log(
  `Wrote ${(bytes / 1024).toFixed(0)} KB: ${stocks.length} stocks · ${Object.keys(metrics).length} metrics · ${Object.keys(detail).length} details · ${signals.length} signals · ${activity.length} activity · ${accounts.length} accounts`,
);
