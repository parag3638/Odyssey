"use client";

import type { IndustryRow } from "@/lib/api";
import { Select } from "@/components/ui";
import { SearchIcon, XIcon } from "@/components/icons";

export interface FilterState {
  sector: string;
  industry: string;
  q: string;
}

export function IndustryFilter({
  industries,
  value,
  onChange,
}: {
  industries: IndustryRow[];
  value: FilterState;
  onChange: (patch: Partial<FilterState>) => void;
}) {
  const sectors = Array.from(new Set(industries.map((i) => i.sector).filter(Boolean))).sort();
  const inds = Array.from(
    new Set(
      industries
        .filter((i) => !value.sector || i.sector === value.sector)
        .map((i) => i.industry),
    ),
  ).sort();

  return (
    <div className="filters">
      <span className="filter">
        Sector
        <Select
          ariaLabel="Sector"
          minWidth={160}
          value={value.sector}
          onChange={(sector) => onChange({ sector, industry: "" })}
          options={[
            { value: "", label: "All sectors" },
            ...sectors.map((s) => ({ value: s, label: s })),
          ]}
        />
      </span>
      <span className="filter">
        Industry
        <Select
          ariaLabel="Industry"
          minWidth={180}
          value={value.industry}
          onChange={(industry) => onChange({ industry })}
          options={[
            { value: "", label: "All industries" },
            ...inds.map((i) => ({ value: i, label: i })),
          ]}
        />
      </span>
      <span className="sp" />
      <div className="searchbar" style={{ minWidth: 260 }}>
        <SearchIcon />
        <input
          placeholder="Search symbol or name"
          value={value.q}
          onChange={(e) => onChange({ q: e.target.value })}
          style={{ height: "auto", border: 0, background: "transparent", padding: 0, flex: 1 }}
        />
        {value.q && (
          <button
            type="button"
            className="clr"
            aria-label="Clear search"
            onClick={() => onChange({ q: "" })}
            style={{ color: "var(--text-3)", display: "flex" }}
          >
            <XIcon />
          </button>
        )}
      </div>
    </div>
  );
}
