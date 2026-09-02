import { useEffect, useMemo, useState } from "react";
import { exportRowsToCsv, exportRowsToExcel } from "../utils/export.js";
import { useClickOutside } from "../utils/useClickOutside.js";
import { Columns3, FileSpreadsheet, FileText } from "./icons.jsx";
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

function ColumnsMenu({ cols, visibleKeys, onToggle, onShowAll, onReset }) {
  const [open, setOpen] = useState(false);
  const ref = useClickOutside(() => setOpen(false));

  return (
    <div className="col-filter" ref={ref}>
      <button type="button" className="btn secondary xs" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <Columns3 size={13} />
        Columns
      </button>
      {open && (
        <div className="col-filter-menu columns-menu" role="menu">
          <div className="col-filter-footer" style={{ paddingTop: 0 }}>
            <button type="button" className="link-btn" onClick={onShowAll}>
              Show all
            </button>
            <button type="button" className="link-btn" onClick={onReset}>
              Reset
            </button>
          </div>
          <div className="col-filter-divider" />
          <div className="col-filter-values">
            {cols.map((col) => {
              const key = colKey(col);
              return (
                <label key={key} className="col-filter-value">
                  <input type="checkbox" checked={visibleKeys.has(key)} onChange={() => onToggle(key)} />
                  <span>{colLabel(col)}</span>
                </label>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// Rendering every row into the DOM at once (a fetch can return many
// thousands of raw fills) is what was locking up / crashing the tab.
// Render a bounded window and let the user page in more on demand.
const PAGE_SIZE = 200;

export default function DataTable({ rows, columns, keyField, exportFilename = "export", initialVisibleKeys }) {
  const cols = columns || (rows && rows.length > 0 ? Object.keys(rows[0]) : []);

  const [visibleKeys, setVisibleKeys] = useState(() => new Set(initialVisibleKeys || cols.map(colKey)));
  const [columnFilters, setColumnFilters] = useState({}); // key -> Set<string> | null(=no filter, omitted)
  const [sort, setSort] = useState({ key: null, dir: "asc" });
  const [renderLimit, setRenderLimit] = useState(PAGE_SIZE);

  const visibleCols = cols.filter((c) => visibleKeys.has(colKey(c)));

  // Unique display values per column, computed once from the full dataset
  // (not the currently-filtered rows) — matches how spreadsheet filters work.
  const uniqueValuesByKey = useMemo(() => {
    const map = {};
    for (const col of visibleCols) {
      const key = colKey(col);
      const set = new Set();
      for (const row of rows) set.add(displayValue(col, row));
      map[key] = [...set].sort((a, b) => a.localeCompare(b));
    }
    return map;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, visibleKeys]);

  const activeFilterKeys = Object.keys(columnFilters).filter((k) => columnFilters[k] !== null && columnFilters[k] !== undefined);

  const visibleRows = useMemo(() => {
    if (!rows) return [];
    let result = rows;

    for (const col of visibleCols) {
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
  }, [rows, columnFilters, sort, visibleKeys]);

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

  const headers = visibleCols.map(colLabel);
  const exportRows = () => visibleRows.map((row) => visibleCols.map((col) => displayValue(col, row)));
  const handleExportCsv = () => exportRowsToCsv(headers, exportRows(), exportFilename);
  const handleExportExcel = () => exportRowsToExcel(headers, exportRows(), exportFilename);

  return (
    <div className="datatable">
      <div className="datatable-toolbar">
        <div className="datatable-toolbar-left">
          <span className="datatable-count">
            {visibleRows.length.toLocaleString()} / {rows.length.toLocaleString()}
          </span>
          {hasActiveState && (
            <button type="button" className="link-btn" onClick={clearAll}>
              Clear all
            </button>
          )}
        </div>

        <div className="datatable-toolbar-right">
          <ColumnsMenu
            cols={cols}
            visibleKeys={visibleKeys}
            onToggle={(key) =>
              setVisibleKeys((prev) => {
                const next = new Set(prev);
                if (next.has(key)) next.delete(key);
                else next.add(key);
                return next;
              })
            }
            onShowAll={() => setVisibleKeys(new Set(cols.map(colKey)))}
            onReset={() => setVisibleKeys(new Set(initialVisibleKeys || cols.map(colKey)))}
          />
          <button type="button" className="btn secondary xs" onClick={handleExportCsv} title="Export CSV">
            <FileText size={13} />
            CSV
          </button>
          <button type="button" className="btn secondary xs" onClick={handleExportExcel} title="Export Excel">
            <FileSpreadsheet size={13} />
            Excel
          </button>
        </div>
      </div>

      <div className={`table-wrap${hasMore ? " table-wrap--attached" : ""}`}>
        <table>
          <thead>
            <tr>
              {visibleCols.map((col) => {
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
                {visibleCols.map((col) => {
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
                <td colSpan={visibleCols.length}>
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
