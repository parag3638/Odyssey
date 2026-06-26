"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { PaperBadge } from "./PaperBadge";
import { listAccounts } from "@/lib/api";
import { initials as toInitials } from "@/lib/format";
import {
  ActivityIcon,
  ChevronDownIcon,
  SailIcon,
  OverviewIcon,
  PositionsIcon,
  SearchIcon,
  SignalsIcon,
  StocksIcon,
} from "./icons";

type NavKey =
  | "overview"
  | "stocks"
  | "positions"
  | "signals"
  | "activity";

const PRIMARY: { key: NavKey; label: string; icon: React.ReactNode; href: string }[] = [
  { key: "overview", label: "Home", icon: <OverviewIcon />, href: "/" },
  { key: "stocks", label: "Stocks", icon: <StocksIcon />, href: "/stocks" },
];

const PORTFOLIO: { key: NavKey; label: string; icon: React.ReactNode; href: string }[] = [
  { key: "positions", label: "Positions", icon: <PositionsIcon />, href: "/positions" },
  { key: "signals", label: "Signals", icon: <SignalsIcon />, href: "/signals" },
  { key: "activity", label: "Activity", icon: <ActivityIcon />, href: "/activity" },
];

export function Nav({
  accountLabel,
  accountInitials,
  active = "overview",
}: {
  accountLabel?: string;
  accountInitials?: string;
  active?: NavKey;
}) {
  const router = useRouter();
  const [date, setDate] = useState("");
  const [marketOpen, setMarketOpen] = useState<boolean | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  // Real account, fetched once so every screen is consistent.
  const [acctLabel, setAcctLabel] = useState(accountLabel ?? "Account");
  const [acctInitials, setAcctInitials] = useState(accountInitials ?? "··");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const a = await listAccounts();
        if (!cancelled && a[0]) {
          setAcctLabel(a[0].label);
          setAcctInitials(toInitials(a[0].label));
        }
      } catch {
        /* keep fallback */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const id = requestAnimationFrame(() => {
      setDate(
        new Date().toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" }),
      );
    });
    return () => cancelAnimationFrame(id);
  }, []);

  // Real US-market status (regular session, ET, weekdays 9:30–16:00). Computed
  // client-side after mount to avoid a hydration mismatch; refreshes each minute.
  useEffect(() => {
    const compute = () => {
      const parts = new Intl.DateTimeFormat("en-US", {
        timeZone: "America/New_York",
        weekday: "short",
        hour: "2-digit",
        minute: "2-digit",
        hourCycle: "h23",
      }).formatToParts(new Date());
      const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
      const weekday = get("weekday");
      const mins = Number(get("hour")) * 60 + Number(get("minute"));
      const isWeekday = !["Sat", "Sun"].includes(weekday);
      setMarketOpen(isWeekday && mins >= 570 && mins < 960);
    };
    compute();
    const id = setInterval(compute, 60_000);
    return () => clearInterval(id);
  }, []);

  // "/" focuses search anywhere (unless already typing) → the stock finder.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "/") return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      e.preventDefault();
      router.push("/stocks");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [router]);

  useEffect(() => {
    if (!menuOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [menuOpen]);

  const go = (href: string) => router.push(href);
  // const ThemeIcon = theme === "dark" ? SunIcon : MoonIcon; // theme toggle disabled
  const portfolioActive = PORTFOLIO.some((i) => i.key === active);

  return (
    <>
      {/* Labeled top bar — shown ≤1280px */}
      <header className="topnav">
        <button type="button" className="flame" onClick={() => go("/")} aria-label="Home">
          <SailIcon />
        </button>
        <nav className="tn-links">
          {PRIMARY.map((item) => (
            <button
              key={item.key}
              type="button"
              className={`tn${item.key === active ? " on" : ""}`}
              onClick={() => go(item.href)}
            >
              {item.label}
            </button>
          ))}
          <span className="tn-drop" ref={menuRef}>
            <button
              type="button"
              className={`tn drop${portfolioActive ? " on" : ""}`}
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((o) => !o)}
            >
              Portfolio
              <ChevronDownIcon />
            </button>
            {menuOpen && (
              <div className="navmenu" role="menu">
                {PORTFOLIO.map((item) => (
                  <button
                    key={item.key}
                    type="button"
                    className={item.key === active ? "on" : ""}
                    onClick={() => {
                      setMenuOpen(false);
                      go(item.href);
                    }}
                  >
                    {item.icon}
                    {item.label}
                  </button>
                ))}
              </div>
            )}
          </span>
        </nav>
        <span className="sp" />
        <span className="mkt">
          {date}
          {marketOpen != null && (
            <>
              <span className={marketOpen ? "dotg" : "dotc"} />{" "}
              {marketOpen ? "Markets open" : "Markets closed"}
            </>
          )}
        </span>
        <button
          type="button"
          className="searchbar"
          style={{ cursor: "pointer" }}
          onClick={() => go("/stocks")}
          aria-label="Search stocks"
        >
          <SearchIcon />
          Search name or symbol
          <span className="kbd">/</span>
        </button>
        <PaperBadge />
        <button
          className="acct-pill"
          type="button"
          title={acctLabel}
          aria-label={`Account: ${acctLabel}`}
          onClick={() => go("/positions")}
        >
          <span className="av">{acctInitials}</span>
        </button>
      </header>

      {/* Left icon rail — shown ≥1281px */}
      <aside className="leftrail">
        <button type="button" className="flame" onClick={() => go("/")} aria-label="Home" data-tip="Home">
          <SailIcon />
        </button>
        {PRIMARY.map((item) => (
          <button
            key={item.key}
            type="button"
            className={`lr${item.key === active ? " on" : ""}`}
            data-tip={item.label}
            aria-label={item.label}
            onClick={() => go(item.href)}
          >
            {item.icon}
          </button>
        ))}
        <span className="rail-div" />
        {PORTFOLIO.map((item) => (
          <button
            key={item.key}
            type="button"
            className={`lr${item.key === active ? " on" : ""}`}
            data-tip={item.label}
            aria-label={item.label}
            onClick={() => go(item.href)}
          >
            {item.icon}
          </button>
        ))}
        <span className="sp" />
        <button
          className="lr av"
          type="button"
          data-tip={acctLabel}
          aria-label={`Account: ${acctLabel}`}
          onClick={() => go("/positions")}
        >
          {acctInitials}
        </button>
      </aside>
    </>
  );
}
