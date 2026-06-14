"use client";

import { useEffect, useRef, useState } from "react";
import { CheckIcon, ColumnsIcon, GripIcon } from "@/components/icons";

export interface ColumnSpec {
  key: string;
  label: string;
  visible: boolean;
  locked?: boolean; // can't be hidden (e.g. the first identity column)
}

export function ColumnCustomizer({
  columns,
  onChange,
}: {
  columns: ColumnSpec[];
  onChange: (cols: ColumnSpec[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [drag, setDrag] = useState<number | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node))
        setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const toggle = (key: string) =>
    onChange(
      columns.map((c) =>
        c.key === key && !c.locked ? { ...c, visible: !c.visible } : c,
      ),
    );

  const move = (from: number, to: number) => {
    if (from === to) return;
    const next = [...columns];
    const [item] = next.splice(from, 1);
    next.splice(to, 0, item);
    onChange(next);
  };

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button
        type="button"
        className="icon-btn round"
        aria-label="Customize columns"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <ColumnsIcon />
      </button>

      {open && (
        <div className="colcust" role="menu">
          <div className="grp bd">Columns</div>
          {columns.map((c, i) => (
            <div
              key={c.key}
              className={`colrow${c.visible ? "" : " off"}`}
              draggable
              onDragStart={() => setDrag(i)}
              onDragOver={(e) => e.preventDefault()}
              onDrop={() => {
                if (drag != null) move(drag, i);
                setDrag(null);
              }}
            >
              <span className="grab">
                <GripIcon />
              </span>
              <span className="nm">{c.label}</span>
              <button
                type="button"
                className={`checkbox${c.visible ? " on" : ""}`}
                aria-label={`${c.visible ? "Hide" : "Show"} ${c.label}`}
                aria-pressed={c.visible}
                disabled={c.locked}
                onClick={() => toggle(c.key)}
              >
                {c.visible && <CheckIcon />}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
