import { useEffect, useMemo, useState } from "react";
import { exportRowsToCsv, exportRowsToExcel } from "../utils/export.js";
import { useClickOutside } from "../utils/useClickOutside.js";
import { FileSpreadsheet, FileText, Download } from "./icons.jsx";
import ColumnFilterMenu from "./ColumnFilterMenu.jsx";

// Always a string: downstream filter/sort/search logic (localeCompare,
// toLowerCase, Set membership) assumes text, and React renders a stringified
// number identically to the number itself, so there's no display trade-off.
function formatCell(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function colKey(col) {
  return typeof col === "string" ? col : col.key;
}

function colLabel(col) {
  return typeof col === "string" ? col : col.label || col.key;
}

function colAlign(col) {
  return typeof col === "string" ? "left" : col.align || "left";
}

// Numeric strings (e.g. nanosecond timestamps) should sort numerically, not lexically.
function sortValue(value) {
  if (typeof value === "string" && value.trim() !== "" && !Number.isNaN(Number(value))) {
    return Number(value);
  }
  return value;
}

// Display text for a cell: what the user sees, filters against, and exports —
// the rendered value (e.g. side -> "Buy"/"Sell") when a column defines one.
function displayValue(col, row) {
  if (typeof col !== "string" && col.render) {
    const rendered = col.render(row);
    if (typeof rendered === "string" || typeof rendered === "number") return String(rendered);
  }
  return formatCell(row[colKey(col)]);
}

// A single Excel-style icon button that opens a small "CSV / Excel" choice —
// meant to sit in a page's own toolbar row (see FillsPage.jsx), not tied to
// DataTable's internal filter/sort state, so it exports exactly `rows` as
// given rather than needing to reach into the table's live state.
export function ExportMenu({ rows, columns, filename = "export", style }) {
  const [open, setOpen] = useState(false);
  const ref = useClickOutside(() => setOpen(false));

  const headers = columns.map(colLabel);
  const buildRows = () => rows.map((row) => columns.map((col) => displayValue(col, row)));

  const handleCsv = () => {
    exportRowsToCsv(headers, buildRows(), filename);
    setOpen(false);
  };
  const handleExcel = () => {
    exportRowsToExcel(headers, buildRows(), filename);
    setOpen(false);
  };

  return (
    <div className="col-filter" ref={ref} style={{ position: "relative", ...style }}>
      <button
        type="button"
        className="btn secondary xs icon-only"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-label="Export"
        title="Export"
      >
        <Download size={15} />
      </button>
      {open && (
        <div className="col-filter-menu export-menu" role="menu">
          <button type="button" className="col-filter-sort-item" role="menuitem" onClick={handleCsv}>
            <FileText size={13} />
            CSV
          </button>
          <button type="button" className="col-filter-sort-item" role="menuitem" onClick={handleExcel}>
            <FileSpreadsheet size={13} />
            Excel
          </button>
        </div>
      )}
    </div>
  );
}

// Rendering every row into the DOM at once (a fetch can return many
// thousands of raw fills) is what was locking up / crashing the tab.
// Render a bounded window and let the user page in more on demand.
const PAGE_SIZE = 200;

export default function DataTable({ rows, columns, keyField }) {
  const cols = columns || (rows && rows.length > 0 ? Object.keys(rows[0]) : []);

  const [columnFilters, setColumnFilters] = useState({}); // key -> Set<string> | null(=no filter, omitted)
  const [sort, setSort] = useState({ key: null, dir: "asc" });
  const [renderLimit, setRenderLimit] = useState(PAGE_SIZE);

  // Unique display values per column, computed once from the full dataset
  // (not the currently-filtered rows) — matches how spreadsheet filters work.
  const uniqueValuesByKey = useMemo(() => {
    const map = {};
    for (const col of cols) {
      const key = colKey(col);
      const set = new Set();
      for (const row of rows) set.add(displayValue(col, row));
      map[key] = [...set].sort((a, b) => a.localeCompare(b));
    }
    return map;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows]);

  const activeFilterKeys = Object.keys(columnFilters).filter((k) => columnFilters[k] !== null && columnFilters[k] !== undefined);

  const visibleRows = useMemo(() => {
    if (!rows) return [];
    let result = rows;

    for (const col of cols) {
      const key = colKey(col);
      const selected = columnFilters[key];
      if (selected) {
        result = result.filter((row) => selected.has(displayValue(col, row)));
      }
    }

    if (sort.key) {
      result = [...result].sort((a, b) => {
        const av = sortValue(a[sort.key]);
        const bv = sortValue(b[sort.key]);
        if (av === null || av === undefined) return 1;
        if (bv === null || bv === undefined) return -1;
        const cmp = typeof av === "number" && typeof bv === "number" ? av - bv : String(av).localeCompare(String(bv));
        return sort.dir === "asc" ? cmp : -cmp;
      });
    }

    return result;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, columnFilters, sort]);

  // Reset the render window whenever the filtered/sorted result set changes,
  // so paging state never points past the end of a new, smaller result.
  useEffect(() => {
    setRenderLimit(PAGE_SIZE);
  }, [rows, columnFilters, sort]);

  if (!rows || rows.length === 0) {
    return <p className="status">No data.</p>;
  }

  const pagedRows = visibleRows.slice(0, renderLimit);
  const hasMore = renderLimit < visibleRows.length;

  const setColumnFilter = (key, selectedSet) => {
    setColumnFilters((prev) => {
      const next = { ...prev };
      if (selectedSet === null) delete next[key];
      else next[key] = selectedSet;
      return next;
    });
  };

  const setColumnSort = (key, dir) => setSort({ key, dir });

  const clearAll = () => {
    setColumnFilters({});
    setSort({ key: null, dir: "asc" });
  };

  const hasActiveState = activeFilterKeys.length > 0 || sort.key !== null;

  return (
    <div className="datatable">
      <div className="datatable-table-area">
        {hasActiveState && (
          <div className="datatable-floating-controls">
            <button type="button" className="link-btn datatable-clear-all" onClick={clearAll}>
              Clear all
            </button>
          </div>
        )}

        <div className={`table-wrap table-wrap--fit${hasMore ? " table-wrap--attached" : ""}`}>
        <table>
          <thead>
            <tr>
              {cols.map((col) => {
                const key = colKey(col);
                const align = colAlign(col);
                const active = sort.key === key;
                return (
                  <th key={key} aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}>
                    <div className="th-inner" style={{ justifyContent: align === "right" ? "flex-end" : "space-between" }}>
                      <span className="th-label" title={colLabel(col)}>
                        {colLabel(col)}
                        {active && <span className="th-sort-indicator">{sort.dir === "asc" ? "▲" : "▼"}</span>}
                      </span>
                      <ColumnFilterMenu
                        values={uniqueValuesByKey[key] || []}
                        selected={columnFilters[key] ?? null}
                        onChange={(sel) => setColumnFilter(key, sel)}
                        sortDir={active ? sort.dir : null}
                        onSort={(dir) => setColumnSort(key, dir)}
                      />
                    </div>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {pagedRows.map((row, i) => (
              <tr key={keyField ? row[keyField] : i}>
                {cols.map((col) => {
                  const key = colKey(col);
                  return (
                    <td key={key} style={{ textAlign: colAlign(col) }}>
                      {col.render ? col.render(row) : formatCell(row[key])}
                    </td>
                  );
                })}
              </tr>
            ))}
            {visibleRows.length === 0 && (
              <tr>
                <td colSpan={cols.length}>
                  <div className="empty-state">
                    <p className="status">No rows match the current filters.</p>
                    <button type="button" className="link-btn" onClick={clearAll}>
                      Clear all
                    </button>
                  </div>
                </td>
              </tr>
            )}
          </tbody>
        </table>
        </div>
      </div>
      {hasMore && (
        <div className="load-more-row">
          <span className="status" style={{ margin: 0 }}>
            Showing {pagedRows.length.toLocaleString()} of {visibleRows.length.toLocaleString()}
          </span>
          <button type="button" className="link-btn" onClick={() => setRenderLimit((n) => n + PAGE_SIZE)}>
            Load {Math.min(PAGE_SIZE, visibleRows.length - renderLimit).toLocaleString()} more
          </button>
          <button type="button" className="link-btn" onClick={() => setRenderLimit(visibleRows.length)}>
            Load all
          </button>
        </div>
      )}
    </div>
  );
}
