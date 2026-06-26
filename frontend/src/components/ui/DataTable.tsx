"use client";

import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import { Skeleton } from "./primitives";
import { ChevronLeftIcon, ChevronRightIcon } from "@/components/icons";

export interface Column<Row> {
  key: string;
  header: ReactNode;
  align?: "l" | "r";
  sortable?: boolean;
  sortValue?: (row: Row) => number | string;
  render: (row: Row) => ReactNode;
}

export interface DataTableProps<Row> {
  columns: Column<Row>[];
  rows: Row[];
  rowKey: (row: Row) => string;
  onRowClick?: (row: Row) => void;
  /** ordered list of column keys to show (from ColumnCustomizer) */
  visibleKeys?: string[];
  initialSort?: { key: string; dir: "asc" | "desc" };
  empty?: ReactNode;
  loading?: boolean;
  skeletonRows?: number;
  activeKey?: (row: Row) => boolean;
  /** rows per page; omit for no pagination */
  pageSize?: number;

  /* ---- controlled mode (parent owns sort + pagination) ----
     Provide these when the parent must know the current page's rows ahead of
     render — e.g. to fetch per-page data. When `onSortChange`/`onPageChange`
     are passed, the table renders `rows` as-is (already sorted + sliced by the
     parent) and reports interactions instead of mutating internal state. */
  sort?: Sort | null;
  onSortChange?: (sort: Sort) => void;
  page?: number;
  pageCount?: number;
  total?: number;
  onPageChange?: (page: number) => void;
}

type Sort = { key: string; dir: "asc" | "desc" };

export function DataTable<Row>({
  columns,
  rows,
  rowKey,
  onRowClick,
  visibleKeys,
  initialSort,
  empty,
  loading,
  skeletonRows = 5,
  activeKey,
  pageSize,
  sort: sortProp,
  onSortChange,
  page: pageProp,
  pageCount: pageCountProp,
  total: totalProp,
  onPageChange,
}: DataTableProps<Row>) {
  const controlledSort = onSortChange != null;
  const controlledPage = onPageChange != null;

  const [internalSort, setInternalSort] = useState<Sort | null>(
    initialSort ?? null,
  );
  const [internalPage, setInternalPage] = useState(0);

  const sort = controlledSort ? sortProp ?? null : internalSort;

  const cols = useMemo(() => {
    if (!visibleKeys) return columns;
    const byKey = new Map(columns.map((c) => [c.key, c]));
    return visibleKeys
      .map((k) => byKey.get(k))
      .filter((c): c is Column<Row> => Boolean(c));
  }, [columns, visibleKeys]);

  // In controlled mode the parent already sorted `rows`; never re-sort here.
  const sorted = useMemo(() => {
    if (controlledSort || !sort) return rows;
    const col = columns.find((c) => c.key === sort.key);
    if (!col?.sortValue) return rows;
    const sv = col.sortValue;
    const dir = sort.dir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      const va = sv(a);
      const vb = sv(b);
      if (typeof va === "number" && typeof vb === "number")
        return (va - vb) * dir;
      return String(va).localeCompare(String(vb)) * dir;
    });
  }, [rows, sort, columns, controlledSort]);

  const toggleSort = (key: string) => {
    const cur = sort;
    const next: Sort =
      cur?.key === key
        ? { key, dir: cur.dir === "asc" ? "desc" : "asc" }
        : { key, dir: "desc" };
    if (controlledSort) {
      onSortChange(next);
    } else {
      setInternalPage(0);
      setInternalSort(next);
    }
  };

  const span = cols.length;
  // Controlled: parent reports the full count + page geometry, and `rows` is
  // already the current slice. Uncontrolled: derive everything from `sorted`.
  const total = controlledPage ? totalProp ?? rows.length : sorted.length;
  const pageCount = controlledPage
    ? Math.max(1, pageCountProp ?? 1)
    : pageSize
      ? Math.max(1, Math.ceil(total / pageSize))
      : 1;
  const curPage = controlledPage
    ? pageProp ?? 0
    : Math.min(internalPage, pageCount - 1); // clamp when results shrink
  const paged = controlledPage
    ? rows
    : pageSize
      ? sorted.slice(curPage * pageSize, curPage * pageSize + pageSize)
      : sorted;
  const goToPage = (p: number) => {
    const clamped = Math.max(0, Math.min(pageCount - 1, p));
    if (controlledPage) onPageChange(clamped);
    else setInternalPage(clamped);
  };
  const showPager = controlledPage
    ? pageCount > 1
    : pageSize != null && !loading && total > pageSize;

  return (
    <div className="tcard">
      <table>
        <thead>
          <tr>
            {cols.map((c, i) => {
              const left = c.align === "l" || (i === 0 && c.align !== "r");
              const isSort = sort?.key === c.key;
              return (
                <th
                  key={c.key}
                  className={`${left ? "l" : ""}${c.sortable ? " sortable" : ""}${
                    isSort ? " sortcol" : ""
                  }`}
                  onClick={c.sortable ? () => toggleSort(c.key) : undefined}
                >
                  {c.header}
                  {c.sortable && (
                    <span className="sort-ar">
                      {isSort ? (sort!.dir === "asc" ? "↑" : "↓") : "↕"}
                    </span>
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {loading &&
            Array.from({ length: skeletonRows }).map((_, r) => (
              <tr key={`sk${r}`}>
                {cols.map((c, i) => (
                  <td key={c.key} className={i === 0 ? "l" : ""}>
                    {i === 0 ? (
                      // match the real first cell (logo + label) so skeleton rows
                      // are the same height as loaded rows — keeps columns balanced.
                      <span style={{ display: "inline-flex", alignItems: "center", gap: 10, lineHeight: 0 }}>
                        <Skeleton w={30} h={30} r={15} />
                        <Skeleton w={104} h={12} />
                      </span>
                    ) : (
                      <Skeleton w={60} h={12} style={{ marginLeft: "auto" }} />
                    )}
                  </td>
                ))}
              </tr>
            ))}

          {!loading && sorted.length === 0 && (
            <tr className="empty-row">
              <td colSpan={span}>{empty ?? "Nothing to show."}</td>
            </tr>
          )}

          {!loading &&
            paged.map((row) => (
              <tr
                key={rowKey(row)}
                className={`${onRowClick ? "clickable" : ""}${
                  activeKey?.(row) ? " active" : ""
                }`}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
              >
                {cols.map((c, i) => {
                  const left = c.align === "l" || (i === 0 && c.align !== "r");
                  return (
                    <td key={c.key} className={left ? "l" : ""}>
                      {c.render(row)}
                    </td>
                  );
                })}
              </tr>
            ))}
        </tbody>
      </table>

      {showPager && (
        <div className="tbl-pager">
          <span>
            {controlledPage
              ? `${total.toLocaleString()} results`
              : `Showing ${curPage * pageSize! + 1}–${Math.min(
                  total,
                  curPage * pageSize! + pageSize!,
                )} of ${total}`}
          </span>
          <div className="pg-btns">
            <button
              type="button"
              aria-label="Previous page"
              disabled={curPage === 0}
              onClick={() => goToPage(curPage - 1)}
            >
              <ChevronLeftIcon />
            </button>
            <span className="pg-num">
              {curPage + 1} / {pageCount}
            </span>
            <button
              type="button"
              aria-label="Next page"
              disabled={curPage >= pageCount - 1}
              onClick={() => goToPage(curPage + 1)}
            >
              <ChevronRightIcon />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
