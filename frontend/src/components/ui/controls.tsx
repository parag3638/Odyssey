"use client";

import { useState } from "react";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { SearchIcon } from "@/components/icons";
import { Button as ShadcnButton } from "./button";
import {
  Tabs as ShadcnTabs,
  TabsList as ShadcnTabsList,
  TabsTrigger as ShadcnTabsTrigger,
} from "./tabs";
import {
  Select as ShadcnSelect,
  SelectContent as ShadcnSelectContent,
  SelectItem as ShadcnSelectItem,
  SelectTrigger as ShadcnSelectTrigger,
  SelectValue as ShadcnSelectValue,
} from "./select";

/* ---------------- Button ----------------
   Wraps shadcn's Button (for its Slot/asChild plumbing) but keeps this
   app's own literal .btn/.btn.<variant> classes doing all the actual
   visual work — components.css is imported unlayered, so it always wins
   over shadcn's Tailwind-utility classes in the cascade regardless, but
   we pass shadcn's own default-variant classes through anyway for
   consistency with future shadcn-native consumers. Prop API unchanged so
   no call site in the app needs to change. */
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
    <ShadcnButton className={`btn${v}${sm ? " sm" : ""} ${className}`} {...rest}>
      {children}
    </ShadcnButton>
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
    <ShadcnTabs value={value} onValueChange={(v) => onChange(v as T)}>
      <ShadcnTabsList className={`tabs${ghost ? " ghost" : ""}`}>
        {asOptions(options).map((o) => (
          <ShadcnTabsTrigger
            key={o.value}
            value={o.value}
            className={`tab${o.value === value ? " on" : ""}`}
          >
            {o.label}
          </ShadcnTabsTrigger>
        ))}
      </ShadcnTabsList>
    </ShadcnTabs>
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

/* ---------------- Select ----------------
   Wraps shadcn's Select (Radix's APG-compliant combobox — keyboard nav,
   type-ahead, portal, collision-aware flip positioning all come from Radix
   natively now, replacing what used to be ~150 lines of hand-rolled
   equivalents here). Prop API unchanged so no call site needs to change. */
export function Select<T extends string>({
  value,
  options,
  onChange,
  placeholder = "Select…",
  ariaLabel,
  id,
  minWidth,
  disabled,
  className,
}: {
  value: T;
  options: (T | Option<T>)[];
  onChange: (value: T) => void;
  placeholder?: string;
  ariaLabel?: string;
  id?: string;
  minWidth?: number;
  disabled?: boolean;
  className?: string;
}) {
  const opts = asOptions(options);
  const [open, setOpen] = useState(false);

  return (
    <div className={`selectwrap${className ? ` ${className}` : ""}`} style={minWidth ? { minWidth } : undefined}>
      <ShadcnSelect
        value={value}
        onValueChange={(v) => onChange(v as T)}
        disabled={disabled}
        open={open}
        onOpenChange={setOpen}
      >
        <ShadcnSelectTrigger
          id={id}
          aria-label={ariaLabel}
          className={`selectbtn${open ? " open" : ""}`}
        >
          <ShadcnSelectValue placeholder={placeholder} />
        </ShadcnSelectTrigger>
        <ShadcnSelectContent position="popper" className="selectmenu">
          {opts.map((o) => (
            <ShadcnSelectItem key={o.value} value={o.value} className="selectopt">
              {o.label}
            </ShadcnSelectItem>
          ))}
        </ShadcnSelectContent>
      </ShadcnSelect>
    </div>
  );
}
