"use client";

import { useEffect, useState, type RefObject } from "react";

/** How many table rows fit in the remaining viewport height below the
 *  table's top, so a page shows "as many rows as fit" and never needs a
 *  scroll for empty space below it, instead of an arbitrary fixed page size.
 *
 *  Recomputes on window resize AND whenever the table container's own size
 *  changes (via ResizeObserver) — the latter is what catches the loading
 *  skeleton being replaced by real rows, since skeleton and real row
 *  heights can differ and a resize-only listener would miss that entirely,
 *  locking in a stale (usually too-small) count from before data loaded. */
export function useFillRowsState(ref: RefObject<HTMLDivElement | null>) {
  const [rows, setRows] = useState(15);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    // Pager row (~54px) + a little breathing room below it — thead is
    // measured directly below rather than folded into a flat guess, so this
    // constant only has to cover the one piece that can't be measured in
    // advance (the pager doesn't exist in the DOM until pageSize is known).
    const PAGER_AND_MARGIN = 70;
    let raf = 0;
    const calc = () => {
      const el = ref.current;
      if (!el) return;
      const top = el.getBoundingClientRect().top;
      const theadEl = el.querySelector("thead");
      const theadH = theadEl?.getBoundingClientRect().height || 47;
      const rowEl = el.querySelector("tbody tr");
      const rowH = rowEl?.getBoundingClientRect().height || 52;
      const avail = window.innerHeight - top - theadH - PAGER_AND_MARGIN;
      setRows(Math.max(6, Math.floor(avail / rowH)));
      setReady(true);
    };
    const schedule = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(calc);
    };

    schedule();
    window.addEventListener("resize", schedule);

    let ro: ResizeObserver | null = null;
    if (ref.current) {
      ro = new ResizeObserver(schedule);
      ro.observe(ref.current);
    }

    return () => {
      window.removeEventListener("resize", schedule);
      cancelAnimationFrame(raf);
      ro?.disconnect();
    };
  }, [ref]);

  return { rows, ready };
}

export function useFillRows(ref: RefObject<HTMLDivElement | null>) {
  return useFillRowsState(ref).rows;
}
