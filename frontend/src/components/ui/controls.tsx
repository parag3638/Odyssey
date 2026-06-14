"use client";

import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import type { ButtonHTMLAttributes, KeyboardEvent, ReactNode } from "react";
import { createPortal } from "react-dom";
import { CheckIcon, ChevronDownIcon, SearchIcon } from "@/components/icons";

/* ---------------- Button ---------------- */
type Variant = "default" | "primary" | "buy" | "sell" | "ghost";
export function Button({
  variant = "default",
  sm,
  className = "",
  children,
  ...rest
}: {
  variant?: Variant;
  sm?: boolean;
  children: ReactNode;
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  const v = variant === "default" ? "" : ` ${variant}`;
  return (
    <button className={`btn${v}${sm ? " sm" : ""} ${className}`} {...rest}>
      {children}
    </button>
  );
}

export function IconButton({
  round,
  className = "",
  children,
  ...rest
}: {
  round?: boolean;
  children: ReactNode;
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      className={`icon-btn${round ? " round" : ""} ${className}`}
      {...rest}
    >
      {children}
    </button>
  );
}

/* ---------------- Segmented control ---------------- */
export interface Option<T extends string = string> {
  label: string;
  value: T;
}
function asOptions<T extends string>(opts: (T | Option<T>)[]): Option<T>[] {
  return opts.map((o) =>
    typeof o === "string" ? { label: o, value: o } : o,
  );
}

export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
}: {
  options: (T | Option<T>)[];
  value: T;
  onChange: (v: T) => void;
  ariaLabel?: string;
}) {
  return (
    <div className="seg" role="tablist" aria-label={ariaLabel}>
      {asOptions(options).map((o) => (
        <button
          key={o.value}
          type="button"
          role="tab"
          aria-selected={o.value === value}
          className={o.value === value ? "on" : ""}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/* ---------------- Tabs ---------------- */
export function Tabs<T extends string>({
  options,
  value,
  onChange,
  ghost,
}: {
  options: (T | Option<T>)[];
  value: T;
  onChange: (v: T) => void;
  ghost?: boolean;
}) {
  return (
    <div className={`tabs${ghost ? " ghost" : ""}`} role="tablist">
      {asOptions(options).map((o) => (
        <button
          key={o.value}
          type="button"
          role="tab"
          aria-selected={o.value === value}
          className={`tab${o.value === value ? " on" : ""}`}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/* ---------------- Range pills (chart timeframes) ---------------- */
export function Ranges<T extends string>({
  options,
  value,
  onChange,
  hero,
}: {
  options: T[];
  value: T;
  onChange: (v: T) => void;
  hero?: boolean;
}) {
  return (
    <div className={hero ? "heroranges" : "ranges"}>
      {options.map((o) => (
        <button
          key={o}
          type="button"
          className={`${hero ? "hr" : ""}${o === value ? " on" : ""}`.trim()}
          onClick={() => onChange(o)}
        >
          {o}
        </button>
      ))}
    </div>
  );
}

/* ---------------- Search bar ---------------- */
export function SearchBar({
  placeholder = "Search name or symbol",
  kbd = "/",
  className = "",
  onClick,
}: {
  placeholder?: string;
  kbd?: string;
  className?: string;
  onClick?: () => void;
}) {
  return (
    <div className={`searchbar ${className}`} onClick={onClick}>
      <SearchIcon />
      {placeholder}
      {kbd && <span className="kbd">{kbd}</span>}
    </div>
  );
}

/* ---------------- Select (custom listbox, replaces native <select>) ----------
   APG "select-only combobox": a button keeps focus and owns a listbox via
   aria-activedescendant. Keyboard: ↑/↓/Home/End move, Enter/Space select,
   Esc closes, type-ahead jumps. Styled to match the design system. */
export function Select<T extends string>({
  value,
  options,
  onChange,
  placeholder = "Select…",
  ariaLabel,
  id,
  minWidth,
  disabled,
}: {
  value: T;
  options: (T | Option<T>)[];
  onChange: (value: T) => void;
  placeholder?: string;
  ariaLabel?: string;
  id?: string;
  minWidth?: number;
  disabled?: boolean;
}) {
  const opts = asOptions(options);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [pos, setPos] = useState<{
    left: number;
    width: number;
    top?: number;
    bottom?: number;
  } | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const typed = useRef<{ str: string; t: number }>({ str: "", t: 0 });
  const uid = useId();

  const selectedIdx = opts.findIndex((o) => o.value === value);
  const current = selectedIdx >= 0 ? opts[selectedIdx] : null;

  const openMenu = () => {
    if (disabled) return;
    setActive(selectedIdx >= 0 ? selectedIdx : 0);
    setOpen(true);
  };
  const close = () => {
    setOpen(false);
    btnRef.current?.focus();
  };
  const choose = (i: number) => {
    const o = opts[i];
    if (o) onChange(o.value);
    close();
  };

  // Keep the portalled menu glued under the button (fixed → viewport coords),
  // repositioning on scroll/resize so it tracks the button instead of detaching.
  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const r = btnRef.current?.getBoundingClientRect();
      if (!r) return;
      const need = Math.min(280, opts.length * 40 + 12);
      const below = window.innerHeight - r.bottom;
      // flip upward when there isn't room below but there is above
      const up = below < need + 12 && r.top > below;
      setPos(
        up
          ? { left: r.left, width: r.width, bottom: window.innerHeight - r.top + 6 }
          : { left: r.left, width: r.width, top: r.bottom + 6 },
      );
    };
    place();
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [open, opts.length]);

  // Close on outside click (the menu is portalled, so check both refs).
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!rootRef.current?.contains(t) && !listRef.current?.contains(t)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  // keep the active option in view
  useEffect(() => {
    if (open) listRef.current?.querySelector<HTMLElement>(`#${uid}-o${active}`)?.scrollIntoView({ block: "nearest" });
  }, [active, open, uid]);

  const onKeyDown = (e: KeyboardEvent) => {
    if (disabled) return;
    if (!open) {
      if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) {
        e.preventDefault();
        openMenu();
      }
      return;
    }
    switch (e.key) {
      case "ArrowDown": e.preventDefault(); setActive((a) => Math.min(opts.length - 1, a + 1)); break;
      case "ArrowUp": e.preventDefault(); setActive((a) => Math.max(0, a - 1)); break;
      case "Home": e.preventDefault(); setActive(0); break;
      case "End": e.preventDefault(); setActive(opts.length - 1); break;
      case "Enter":
      case " ": e.preventDefault(); choose(active); break;
      case "Escape": e.preventDefault(); close(); break;
      case "Tab": setOpen(false); break;
      default:
        if (e.key.length === 1 && /\S/.test(e.key)) {
          const now = Date.now();
          typed.current = { str: now - typed.current.t < 600 ? typed.current.str + e.key : e.key, t: now };
          const q = typed.current.str.toLowerCase();
          const hit = opts.findIndex((o) => o.label.toLowerCase().startsWith(q));
          if (hit >= 0) setActive(hit);
        }
    }
  };

  return (
    <div className="selectwrap" ref={rootRef} style={minWidth ? { minWidth } : undefined}>
      <button
        ref={btnRef}
        type="button"
        id={id}
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={`${uid}-list`}
        aria-activedescendant={open ? `${uid}-o${active}` : undefined}
        aria-label={ariaLabel}
        disabled={disabled}
        className={`selectbtn${open ? " open" : ""}`}
        onClick={() => (open ? setOpen(false) : openMenu())}
        onKeyDown={onKeyDown}
      >
        <span className={`selectval${current ? "" : " ph"}`}>
          {current ? current.label : placeholder}
        </span>
        <ChevronDownIcon />
      </button>
      {open &&
        pos &&
        createPortal(
          <ul
            ref={listRef}
            id={`${uid}-list`}
            role="listbox"
            aria-label={ariaLabel}
            className="selectmenu"
            style={{ left: pos.left, top: pos.top, bottom: pos.bottom, minWidth: pos.width }}
          >
            {opts.map((o, i) => (
              <li
                key={o.value}
                id={`${uid}-o${i}`}
                role="option"
                aria-selected={o.value === value}
                className={`selectopt${i === active ? " active" : ""}${o.value === value ? " sel" : ""}`}
                onMouseEnter={() => setActive(i)}
                onMouseDown={(e) => e.preventDefault()} // keep focus on the button
                onClick={() => choose(i)}
              >
                <span className="nm">{o.label}</span>
                {o.value === value && <CheckIcon />}
              </li>
            ))}
          </ul>,
          document.body,
        )}
    </div>
  );
}
