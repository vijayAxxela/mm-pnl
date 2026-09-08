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
function defaultSortValue(value) {
  if (typeof value === "string" && value.trim() !== "" && !Number.isNaN(Number(value))) {
    return Number(value);
  }
  return value;
}

// A column can define its own sortValue(row) when the displayed text isn't
// itself sortable — e.g. a "DD-MM-YY HH:MM:SS" string sorts alphabetically
// wrong (day-of-month before year/month), so a column showing that needs to
// hand back the underlying epoch instead (see AnalyzePage.jsx's entry/exit
// time columns).
function getSortValue(col, row) {
  if (typeof col !== "string" && col.sortValue) return col.sortValue(row);
  return defaultSortValue(row[colKey(col)]);
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

// Sum/Avg/Count for a clicked column, over whatever's currently visible
// (post-filter) — raw row[key] values, not the rendered display text, so a
// column with a custom render (e.g. PnL's colored badge) still sums the
// actual number. Non-numeric columns just get a count of non-blank cells.
function computeColumnStats(col, rows) {
  const key = colKey(col);
  let count = 0;
  let sum = 0;
  let numericCount = 0;
  for (const row of rows) {
    const raw = row[key];
    if (raw === null || raw === undefined || raw === "") continue;
    count += 1;
    const n = typeof raw === "number" ? raw : Number(raw);
    if (Number.isFinite(n)) {
      sum += n;
      numericCount += 1;
    }
  }
  const isNumeric = numericCount > 0 && numericCount === count;
  return {
    key,
    label: colLabel(col),
    count,
    sum: isNumeric ? sum : null,
    avg: isNumeric && count > 0 ? sum / count : null,
  };
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

// Renders whatever DataTable's onColumnStatsChange last reported — meant to
// sit in a page's own toolbar row, next to its ExportMenu (see
// FillsPage.jsx/AnalyzePage.jsx), same reasoning as ExportMenu itself living
// outside DataTable: the page controls where it appears.
export function ColumnStatsBadge({ stats, style }) {
  if (!stats) return null;
  const parts = [`Count ${stats.count.toLocaleString()}`];
  if (stats.sum !== null) parts.push(`Sum ${stats.sum.toLocaleString(undefined, { maximumFractionDigits: 2 })}`);
  if (stats.avg !== null) parts.push(`Avg ${stats.avg.toLocaleString(undefined, { maximumFractionDigits: 2 })}`);
  return (
    <span className="column-stats-badge" style={style}>
      <strong>{stats.label}:</strong> {parts.join(" · ")}
    </span>
  );
}

// Rendering every row into the DOM at once (a fetch can return many
// thousands of raw fills) is what was locking up / crashing the tab.
// Render a bounded window and let the user page in more on demand.
const PAGE_SIZE = 200;

export default function DataTable({ rows, columns, keyField, onColumnStatsChange, compact = false, showFilters = true }) {
  const cols = columns || (rows && rows.length > 0 ? Object.keys(rows[0]) : []);

  const [columnFilters, setColumnFilters] = useState({}); // key -> Set<string> | null(=no filter, omitted)
  const [sort, setSort] = useState({ key: null, dir: "asc" });
  const [renderLimit, setRenderLimit] = useState(PAGE_SIZE);
  const [statsKey, setStatsKey] = useState(null); // column key currently showing Sum/Avg/Count, or null
  const [selectedRow, setSelectedRow] = useState(null); // row object reference currently highlighted, or null

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
      const sortCol = cols.find((c) => colKey(c) === sort.key);
      result = [...result].sort((a, b) => {
        const av = getSortValue(sortCol, a);
        const bv = getSortValue(sortCol, b);
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

  // A new dataset (e.g. a fresh fetch) can't contain the same row object
  // reference any more — drop a stale highlight rather than pointing at
  // nothing. Filtering/sorting the SAME dataset keeps the highlight, since
  // the row itself still exists, just possibly reordered/hidden.
  useEffect(() => {
    setSelectedRow(null);
  }, [rows]);

  // Recomputed over whatever's currently visible (post-filter), so toggling
  // a filter updates an already-open Sum/Avg/Count instead of it going stale.
  const columnStats = useMemo(() => {
    if (!statsKey) return null;
    const col = cols.find((c) => colKey(c) === statsKey);
    return col ? computeColumnStats(col, visibleRows) : null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statsKey, visibleRows]);

  useEffect(() => {
    onColumnStatsChange?.(columnStats);
  }, [columnStats, onColumnStatsChange]);

  // Reported to the parent so its own toolbar (e.g. next to the Export
  // button) can render it — a closed table (no rows) never leaves a stale
  // reading behind in that toolbar.
  useEffect(() => {
    return () => onColumnStatsChange?.(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggleStatsColumn = (key) => setStatsKey((prev) => (prev === key ? null : key));

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

  return (
    <div className={`datatable${compact ? " datatable--compact" : ""}`}>
      <div className="datatable-table-area">

        <div className={`table-wrap table-wrap--fit${hasMore ? " table-wrap--attached" : ""}`}>
        <table>
          <thead>
            <tr>
              {cols.map((col) => {
                const key = colKey(col);
                const align = colAlign(col);
                const active = sort.key === key;
                return (
                  <th
                    key={key}
                    className={statsKey === key ? "col-selected" : undefined}
                    aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}
                  >
                    <div className="th-inner" style={{ justifyContent: align === "right" ? "flex-end" : "space-between" }}>
                      <button
                        type="button"
                        className={`th-label th-label-btn${statsKey === key ? " active" : ""}`}
                        title={`${colLabel(col)} — click for Sum/Avg/Count`}
                        onClick={() => toggleStatsColumn(key)}
                      >
                        {colLabel(col)}
                        {active && <span className="th-sort-indicator">{sort.dir === "asc" ? "▲" : "▼"}</span>}
                      </button>
                      {showFilters && (
                        <ColumnFilterMenu
                          values={uniqueValuesByKey[key] || []}
                          selected={columnFilters[key] ?? null}
                          onChange={(sel) => setColumnFilter(key, sel)}
                          sortDir={active ? sort.dir : null}
                          onSort={(dir) => setColumnSort(key, dir)}
                          dateMode={typeof col !== "string" && !!col.dateFilter}
                        />
                      )}
                    </div>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {pagedRows.map((row, i) => (
              <tr
                key={keyField ? row[keyField] : i}
                className={selectedRow === row ? "row-selected" : undefined}
                onClick={() => setSelectedRow((prev) => (prev === row ? null : row))}
              >
                {cols.map((col) => {
                  const key = colKey(col);
                  return (
                    <td
                      key={key}
                      className={statsKey === key ? "col-selected" : undefined}
                      style={{ textAlign: colAlign(col) }}
                    >
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
