import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api.js";
import { ChevronRight } from "./icons.jsx";
import ColumnFilterMenu from "./ColumnFilterMenu.jsx";
import { useClickOutside } from "../utils/useClickOutside.js";

const LEVEL_FIELDS = ["account_name", "exchange", "product_symbol", "contract"];
const HEADERS = ["Account", "Exchange", "Product", "Contract"];

// Account, Exchange, Product, Contract, Buy Qty, Sell Qty, Net, Total PNL,
// Change from Open, Unrealized PNL, Realized PNL, Current Price,
// Avg Open Price, Day Open PNL
const DEFAULT_COL_WIDTHS = ["11%", "8%", "8%", "11%", "6%", "6%", "6%", "7%", "9%", "6%", "6%", "5%", "5%", "6%"];

// This app has no per-user accounts, so the tree's expanded nodes / column
// filters / sort / manually-entered current prices are persisted globally
// under these fixed keys rather than per-user — everyone who opens the PNL
// page sees the same last state.
const UI_STATE_KEY = "pnl-tree";
const PRICES_KEY = "pnl-current-prices";

function serializeTreeState(expanded, columnFilters, sortSpec, contentFit) {
  const filters = {};
  for (const [level, values] of Object.entries(columnFilters)) {
    filters[level] = [...values];
  }
  return { expanded: [...expanded], columnFilters: filters, sortSpec, contentFit };
}

function deserializeTreeState(saved) {
  if (!saved) return null;
  const columnFilters = {};
  for (const [level, values] of Object.entries(saved.columnFilters || {})) {
    columnFilters[level] = new Set(values);
  }
  return {
    expanded: new Set(saved.expanded || []),
    columnFilters,
    sortSpec: saved.sortSpec ?? null,
    contentFit: saved.contentFit ?? false,
  };
}

// There's no live price feed (TT's REST API has none), so "current price"
// is always a manual input, typed in as the DISPLAY price the user sees in
// the TT app (not TT's internal/raw price). avg_open_price and tick_size
// are already scaled by DisplayFactor server-side to match, so no further
// conversion is needed here — this just runs the same tick-based math the
// backend used for realized_pnl.
function computeUnrealizedUsd(row, currentPrice) {
  if (currentPrice === null || currentPrice === undefined || !Number.isFinite(currentPrice)) return 0;
  if (!row.open_qty || !row.tick_size) return 0;
  const direction = row.open_qty > 0 ? 1 : -1;
  const priceDiff = direction * (currentPrice - row.avg_open_price);
  const unrealizedNative = (priceDiff / row.tick_size) * row.tick_value * Math.abs(row.open_qty);
  // direction is -1 for a short position, so a zero price diff (current
  // price == avg open price) multiplies out to -0 — a real JS value that's
  // numerically zero but formats as "-0.00" and would otherwise show a
  // flat position as a loss.
  return unrealizedNative * (row.usd_rate ?? 1) || 0;
}

function makeNode(key, label, level) {
  // avg_open_price/current_price/instrument_id are only ever set on leaf
  // (Contract) nodes — a price can't be meaningfully summed/averaged across
  // different instruments the way qty and PNL can.
  return {
    key,
    label,
    level,
    buy_qty: 0,
    sell_qty: 0,
    open_qty: 0,
    realized_pnl: 0,
    unrealized_pnl: 0,
    day_open_pnl: 0,
    avg_open_price: null,
    current_price: null,
    instrument_id: null,
    children: [],
    childMap: new Map(),
  };
}

function buildTree(rows, currentPrices) {
  const root = makeNode("TOTAL", "TOTAL", -1);

  for (const row of rows) {
    let node = root;
    let keyPath = "TOTAL";
    for (let level = 0; level < LEVEL_FIELDS.length; level++) {
      const value = row[LEVEL_FIELDS[level]];
      keyPath += `/${value}`;
      let child = node.childMap.get(value);
      if (!child) {
        child = makeNode(keyPath, value, level);
        node.childMap.set(value, child);
        node.children.push(child);
      }
      node = child;
    }
    const currentPrice = currentPrices[row.instrument_id];
    node.buy_qty += row.buy_qty;
    node.sell_qty += row.sell_qty;
    node.open_qty += row.open_qty;
    node.realized_pnl += row.realized_pnl;
    node.unrealized_pnl += computeUnrealizedUsd(row, currentPrice);
    node.day_open_pnl += row.day_open_pnl ?? 0;
    node.avg_open_price = row.avg_open_price;
    node.current_price = currentPrice ?? null;
    node.instrument_id = row.instrument_id;
  }

  (function propagate(node) {
    for (const child of node.children) {
      propagate(child);
      node.buy_qty += child.buy_qty;
      node.sell_qty += child.sell_qty;
      node.open_qty += child.open_qty;
      node.realized_pnl += child.realized_pnl;
      node.unrealized_pnl += child.unrealized_pnl;
      node.day_open_pnl += child.day_open_pnl;
    }
  })(root);

  return root;
}

// Change from Open = Total PNL (realized + unrealized) minus Day Open PNL —
// the same "loss since day open" basis the combined-loss alert system uses
// (see backend routes/alerts.py), just per-row instead of summed across
// every account. Day Open PNL is realized-only (see snapshot_day_open_pnl),
// so this is the only place in the tree a real day-open baseline exists.
function changeFromOpenOf(node) {
  return node.realized_pnl + node.unrealized_pnl - node.day_open_pnl;
}

// sortSpec: null | { type: "net"|"pnl"|"unrealized"|"total"|"dayOpen"|"changeFromOpen", dir } | { type: "level", level, dir }
function applySort(node, sortSpec) {
  if (!sortSpec || node.children.length === 0) return node;
  let children = node.children.map((c) => applySort(c, sortSpec));
  const childLevel = node.children[0].level;

  const FIELD_BY_TYPE = { net: "open_qty", pnl: "realized_pnl", unrealized: "unrealized_pnl", dayOpen: "day_open_pnl" };

  if (sortSpec.type in FIELD_BY_TYPE) {
    const field = FIELD_BY_TYPE[sortSpec.type];
    children = [...children].sort((a, b) => (sortSpec.dir === "asc" ? a[field] - b[field] : b[field] - a[field]));
  } else if (sortSpec.type === "total") {
    const totalOf = (n) => n.realized_pnl + n.unrealized_pnl;
    children = [...children].sort((a, b) => (sortSpec.dir === "asc" ? totalOf(a) - totalOf(b) : totalOf(b) - totalOf(a)));
  } else if (sortSpec.type === "changeFromOpen") {
    children = [...children].sort((a, b) =>
      sortSpec.dir === "asc" ? changeFromOpenOf(a) - changeFromOpenOf(b) : changeFromOpenOf(b) - changeFromOpenOf(a),
    );
  } else if (sortSpec.type === "level" && sortSpec.level === childLevel) {
    children = [...children].sort((a, b) =>
      sortSpec.dir === "asc" ? String(a.label).localeCompare(String(b.label)) : String(b.label).localeCompare(String(a.label)),
    );
  }

  return { ...node, children };
}

function formatQty(n) {
  const rounded = Math.round(n * 10000) / 10000;
  const sign = rounded > 0 ? "+" : "";
  return `${sign}${rounded.toLocaleString(undefined, { maximumFractionDigits: 4 })}`;
}

// Buy/Sell totals are always non-negative — no +/- sign needed, unlike Net.
function formatUnsignedQty(n) {
  const rounded = Math.round(n * 10000) / 10000;
  return rounded.toLocaleString(undefined, { maximumFractionDigits: 4 });
}

// n.toFixed(2) on a value that's numerically zero but negatively-signed
// (e.g. -0, or a tiny negative float that rounds to 0.00) prints "-0.00" —
// technically correct JS behavior, but reads as a loss when there isn't
// one. Money/PNL cells should never show a negative zero.
function formatMoney(n) {
  const text = n.toFixed(2);
  return text === "-0.00" ? "0.00" : text;
}

function signClass(n) {
  if (n > 0) return "pos";
  if (n < 0) return "neg";
  return "zero";
}

function Badge({ value, text, bare = false }) {
  return <span className={`pnl-badge ${signClass(value)}${bare ? " bare" : ""}`}>{text}</span>;
}

// Local draft value, separate from the committed price that actually drives
// recalculation — typing doesn't touch Unrealized/Total PNL until Enter or
// blur commits it, instead of recomputing the whole tree on every keystroke.
function PriceInput({ instrumentId, value, onCommit }) {
  const [draft, setDraft] = useState(value ?? "");

  useEffect(() => {
    setDraft(value ?? "");
  }, [value]);

  const commit = () => onCommit(instrumentId, draft);

  // Sized for 8 digits by default, but grows with whatever's actually typed
  // instead of clipping a longer price — the "8 digits" is a floor, not a cap.
  const width = `${Math.max(8, draft.length + 1)}ch`;

  return (
    <input
      type="number"
      step="any"
      inputMode="decimal"
      className="pnl-price-input"
      placeholder="Set price"
      value={draft}
      style={{ width }}
      onChange={(e) => setDraft(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          commit();
          e.currentTarget.blur();
        }
      }}
      onBlur={commit}
    />
  );
}

// Sits on a column's right border only — not the rest of the header, so a
// double-click here doesn't also fire the header's own sort toggle.
// Excel-style: hovering shows the native col-resize cursor as an affordance
// that something's double-clickable there; double-clicking toggles EVERY
// column at once between fixed widths and shrink-to-fit-and-wrap (see
// contentFit in PnlTree — a single global toggle, not per-column).
function ColumnResizeHandle({ onToggle }) {
  return <span className="col-resize-handle" onDoubleClick={onToggle} onClick={(e) => e.stopPropagation()} />;
}

function NodeCells({ node, toggleBtn, onPriceChange }) {
  const colIndex = node.level === -1 ? 0 : node.level;
  const totalPnl = node.realized_pnl + node.unrealized_pnl;
  const change = changeFromOpenOf(node);

  return (
    <>
      {LEVEL_FIELDS.map((_, i) => (
        <td key={i}>
          {i === colIndex && (
            <span className="pnl-tree-cell">
              {toggleBtn}
              <span className="pnl-tree-label" title={String(node.label)}>
                {node.label}
              </span>
            </span>
          )}
        </td>
      ))}
      <td style={{ textAlign: "right" }} className="mono">
        {formatUnsignedQty(node.buy_qty)}
      </td>
      <td style={{ textAlign: "right" }} className="mono">
        {formatUnsignedQty(node.sell_qty)}
      </td>
      <td style={{ textAlign: "right" }}>
        <Badge value={node.open_qty} text={formatQty(node.open_qty)} />
      </td>
      <td style={{ textAlign: "right" }}>
        <Badge value={totalPnl} text={formatMoney(totalPnl)} />
      </td>
      <td style={{ textAlign: "right" }} className="mono">
        <Badge value={change} text={formatMoney(change)} />
      </td>
      <td style={{ textAlign: "right" }}>
        <Badge value={node.unrealized_pnl} text={formatMoney(node.unrealized_pnl)} bare />
      </td>
      <td style={{ textAlign: "right" }}>
        <Badge value={node.realized_pnl} text={formatMoney(node.realized_pnl)} bare />
      </td>
      <td style={{ textAlign: "right" }}>
        {node.level === 3 && (
          <PriceInput instrumentId={node.instrument_id} value={node.current_price} onCommit={onPriceChange} />
        )}
      </td>
      <td style={{ textAlign: "right" }} className="mono">
        {/* Only meaningful per-contract — a price can't be summed/averaged
            across different instruments the way qty and PNL can. */}
        {node.level === 3 ? node.avg_open_price.toFixed(4) : ""}
      </td>
      <td style={{ textAlign: "right" }}>
        <Badge value={node.day_open_pnl} text={formatMoney(node.day_open_pnl)} bare />
      </td>
    </>
  );
}

// TOTAL is a static summary row — it does not gate the account rows behind
// its own expand state; accounts are always shown, each toggled on its own.
function TotalRow({ node, onPriceChange, selectedKey, onSelect }) {
  return (
    <tr
      className={`pnl-row level-${node.level}${selectedKey === node.key ? " selected" : ""}`}
      onClick={() => onSelect(node.key)}
    >
      <NodeCells node={node} toggleBtn={<span className="pnl-tree-toggle-spacer" />} onPriceChange={onPriceChange} />
    </tr>
  );
}

function TreeRows({ node, expanded, toggle, onPriceChange, selectedKey, onSelect, siblingIndex = 0 }) {
  const isExpanded = expanded.has(node.key);
  const hasChildren = node.children.length > 0;
  // Contract (leaf) rows alternate by position among their own product's
  // siblings — not a table-wide nth-child, which can't track "position
  // within this parent" once ancestor rows above are variably
  // expanded/collapsed and shift everything's absolute row index around.
  const isAltLeaf = node.level === 3 && siblingIndex % 2 === 1;
  const isSelected = selectedKey === node.key;

  return (
    <>
      <tr
        className={`pnl-row level-${node.level}${isAltLeaf ? " leaf-alt" : ""}${isSelected ? " selected" : ""}`}
        onClick={() => onSelect(node.key)}
      >
        <NodeCells
          node={node}
          onPriceChange={onPriceChange}
          toggleBtn={
            hasChildren ? (
              <button type="button" className="pnl-tree-toggle" onClick={() => toggle(node.key)}>
                <ChevronRight size={18} strokeWidth={2.75} className={isExpanded ? "rotated" : ""} />
              </button>
            ) : (
              <span className="pnl-tree-toggle-spacer" />
            )
          }
        />
      </tr>
      {hasChildren &&
        isExpanded &&
        node.children.map((child, i) => (
          <TreeRows
            key={child.key}
            node={child}
            expanded={expanded}
            toggle={toggle}
            onPriceChange={onPriceChange}
            selectedKey={selectedKey}
            onSelect={onSelect}
            siblingIndex={i}
          />
        ))}
    </>
  );
}

export default function PnlTree({ rows }) {
  const [expanded, setExpanded] = useState(new Set()); // account/exchange/product keys the user opened
  const [columnFilters, setColumnFilters] = useState({}); // level(0-3) -> Set<string> | undefined(=all)
  const [sortSpec, setSortSpec] = useState(null);
  const [currentPrices, setCurrentPrices] = useState({}); // instrument_id -> number
  const [selectedKey, setSelectedKey] = useState(null); // last-clicked row, transient (not persisted)
  // Excel-style "shrink to fit": off (false) = fixed % column widths. On
  // (true) = every column shrinks to its own single-line content width and
  // the table itself narrows instead of staying stretched to 100%. Global,
  // not per-column — toggled together via any column border's double-click
  // (see ColumnResizeHandle).
  const [contentFit, setContentFit] = useState(false);

  // Guards each save effect from firing with default/empty state before its
  // load below has actually resolved (which would clobber what was saved).
  const loadedRef = useRef(false);
  const saveTimeoutRef = useRef(null);
  const pricesLoadedRef = useRef(false);
  const pricesSaveTimeoutRef = useRef(null);

  useEffect(() => {
    api
      .getUiState(UI_STATE_KEY)
      .then((res) => {
        const restored = deserializeTreeState(res.value);
        if (restored) {
          setExpanded(restored.expanded);
          setColumnFilters(restored.columnFilters);
          setSortSpec(restored.sortSpec);
          setContentFit(restored.contentFit);
        }
      })
      .catch(() => {})
      .finally(() => {
        loadedRef.current = true;
      });

    api
      .getUiState(PRICES_KEY)
      .then((res) => {
        if (res.value) setCurrentPrices(res.value);
      })
      .catch(() => {})
      .finally(() => {
        pricesLoadedRef.current = true;
      });
  }, []);

  useEffect(() => {
    if (!loadedRef.current) return;
    if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current);
    saveTimeoutRef.current = setTimeout(() => {
      api.setUiState(UI_STATE_KEY, serializeTreeState(expanded, columnFilters, sortSpec, contentFit)).catch(() => {});
    }, 500);
    return () => clearTimeout(saveTimeoutRef.current);
  }, [expanded, columnFilters, sortSpec, contentFit]);

  useEffect(() => {
    if (!pricesLoadedRef.current) return;
    if (pricesSaveTimeoutRef.current) clearTimeout(pricesSaveTimeoutRef.current);
    pricesSaveTimeoutRef.current = setTimeout(() => {
      api.setUiState(PRICES_KEY, currentPrices).catch(() => {});
    }, 500);
    return () => clearTimeout(pricesSaveTimeoutRef.current);
  }, [currentPrices]);

  const uniqueValuesByLevel = useMemo(
    () => LEVEL_FIELDS.map((field) => [...new Set(rows.map((r) => String(r[field])))].sort((a, b) => a.localeCompare(b))),
    [rows],
  );

  // Column filters only — this is the "everything that matches what the
  // user asked to see" set, independent of today's activity. Used as the
  // TOTAL row's own basis (see totalsNode below) so a hedge leg sitting
  // flat in one account still counts toward the combined PNL even though
  // its own line isn't worth showing in the list.
  const columnFilteredRows = useMemo(
    () =>
      rows.filter((row) =>
        LEVEL_FIELDS.every((field, i) => {
          const selected = columnFilters[i];
          return !selected || selected.has(String(row[field]));
        }),
      ),
    [rows, columnFilters],
  );

  const filteredRows = useMemo(
    () =>
      // No activity today and no open position — this is a leftover
      // contract row (e.g. a calendar spread from a prior day's roll, or
      // the other leg of a cross-account hedge) with nothing intraday to
      // show. Hidden from the list/tree, but NOT from the TOTAL row's PNL
      // (see totalsNode) — a row with zero buy/sell/net can only have
      // changed today via a fill, and it had none, so its contribution to
      // Change from Open is always exactly 0 regardless; excluding it here
      // only trims the list, it doesn't skew any total.
      columnFilteredRows.filter((row) => !(row.buy_qty === 0 && row.sell_qty === 0 && row.open_qty === 0)),
    [columnFilteredRows],
  );

  const displayRoot = useMemo(
    () => applySort(buildTree(filteredRows, currentPrices), sortSpec),
    [filteredRows, sortSpec, currentPrices],
  );

  // The TOTAL row's own figures come from EVERY column-filtered row, not
  // just the ones with today's activity — see filteredRows above for why
  // that's safe for Change from Open, but Realized/Day Open PNL themselves
  // need the full portfolio (e.g. a hedge leg parked flat in another
  // account) to mean anything as a combined number.
  const totalsNode = useMemo(() => {
    const node = makeNode("TOTAL", "TOTAL", -1);
    for (const row of columnFilteredRows) {
      const currentPrice = currentPrices[row.instrument_id];
      node.buy_qty += row.buy_qty;
      node.sell_qty += row.sell_qty;
      node.open_qty += row.open_qty;
      node.realized_pnl += row.realized_pnl;
      node.unrealized_pnl += computeUnrealizedUsd(row, currentPrice);
      node.day_open_pnl += row.day_open_pnl ?? 0;
    }
    return node;
  }, [columnFilteredRows, currentPrices]);

  // Reveal matching branches when a filter is applied — but through the
  // normal toggle state (union, not an override), so the user can still
  // collapse individual rows afterward instead of every node being forced
  // open for as long as any filter stays active.
  useEffect(() => {
    if (Object.keys(columnFilters).length === 0) return;
    const keysToExpand = new Set();
    for (const row of filteredRows) {
      let path = "TOTAL";
      keysToExpand.add(path);
      // Every ancestor level except the leaf (Contract has no children/toggle).
      for (let level = 0; level < LEVEL_FIELDS.length - 1; level++) {
        path += `/${row[LEVEL_FIELDS[level]]}`;
        keysToExpand.add(path);
      }
    }
    setExpanded((prev) => new Set([...prev, ...keysToExpand]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [columnFilters]);

  const toggle = (key) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const setLevelFilter = (level, selected) => {
    setColumnFilters((prev) => {
      const next = { ...prev };
      if (selected === null) delete next[level];
      else next[level] = selected;
      return next;
    });
  };

  const toggleSimpleSort = (type) =>
    setSortSpec((prev) => {
      if (!prev || prev.type !== type) return { type, dir: "desc" };
      if (prev.dir === "desc") return { type, dir: "asc" };
      return null;
    });

  const toggleContentFit = () => setContentFit((prev) => !prev);

  // Clicking the already-selected row again deselects it; clicking anywhere
  // else in the page (outside the table) also clears the selection instead
  // of leaving a stale row highlighted once the user's moved on.
  const toggleRowSelect = (key) => setSelectedKey((prev) => (prev === key ? null : key));
  const clickOutsideRef = useClickOutside(() => setSelectedKey(null));

  const handlePriceChange = (instrumentId, valueStr) => {
    setCurrentPrices((prev) => {
      const next = { ...prev };
      if (valueStr === "") delete next[instrumentId];
      else next[instrumentId] = parseFloat(valueStr);
      return next;
    });
  };

  const hasActiveFilters = Object.keys(columnFilters).length > 0;

  return (
    <div>
      {hasActiveFilters && (
        <div className="row" style={{ margin: "0 0 var(--space-2)" }}>
          <button type="button" className="link-btn" onClick={() => setColumnFilters({})}>
            Clear all filters
          </button>
        </div>
      )}

      <div className={`table-wrap pnl-tree-wrap${contentFit ? " content-fit" : ""}`} ref={clickOutsideRef}>
        <table className={`pnl-tree-table${contentFit ? " content-fit" : ""}`}>
          {!contentFit && (
            <colgroup>
              {DEFAULT_COL_WIDTHS.map((w, i) => (
                <col key={i} style={{ width: w }} />
              ))}
            </colgroup>
          )}
          <thead>
            <tr>
              {HEADERS.map((h, i) => (
                <th key={h}>
                  <div className="th-inner" style={{ justifyContent: "space-between" }}>
                    <span className="th-label">{h}</span>
                    <ColumnFilterMenu
                      values={uniqueValuesByLevel[i]}
                      selected={columnFilters[i] ?? null}
                      onChange={(sel) => setLevelFilter(i, sel)}
                      sortDir={sortSpec?.type === "level" && sortSpec.level === i ? sortSpec.dir : null}
                      onSort={(dir) => setSortSpec({ type: "level", level: i, dir })}
                    />
                  </div>
                  <ColumnResizeHandle onToggle={toggleContentFit} />
                </th>
              ))}
              <th>
                <div className="th-inner" style={{ justifyContent: "flex-end" }}>
                  <span className="th-label">Buy Qty</span>
                </div>
                <ColumnResizeHandle onToggle={toggleContentFit} />
              </th>
              <th>
                <div className="th-inner" style={{ justifyContent: "flex-end" }}>
                  <span className="th-label">Sell Qty</span>
                </div>
                <ColumnResizeHandle onToggle={toggleContentFit} />
              </th>
              <th>
                <button type="button" className="th-sort" style={{ justifyContent: "flex-end" }} onClick={() => toggleSimpleSort("net")}>
                  Net
                  {sortSpec?.type === "net" && <span className="th-sort-indicator">{sortSpec.dir === "asc" ? "▲" : "▼"}</span>}
                </button>
                <ColumnResizeHandle onToggle={toggleContentFit} />
              </th>
              <th>
                <button type="button" className="th-sort" style={{ justifyContent: "flex-end" }} onClick={() => toggleSimpleSort("total")}>
                  Total PNL
                  {sortSpec?.type === "total" && <span className="th-sort-indicator">{sortSpec.dir === "asc" ? "▲" : "▼"}</span>}
                </button>
                <ColumnResizeHandle onToggle={toggleContentFit} />
              </th>
              <th>
                <button
                  type="button"
                  className="th-sort"
                  style={{ justifyContent: "flex-end" }}
                  onClick={() => toggleSimpleSort("changeFromOpen")}
                >
                  Change from Open
                  {sortSpec?.type === "changeFromOpen" && (
                    <span className="th-sort-indicator">{sortSpec.dir === "asc" ? "▲" : "▼"}</span>
                  )}
                </button>
                <ColumnResizeHandle onToggle={toggleContentFit} />
              </th>
              <th>
                <button
                  type="button"
                  className="th-sort"
                  style={{ justifyContent: "flex-end" }}
                  onClick={() => toggleSimpleSort("unrealized")}
                >
                  Unrealized PNL
                  {sortSpec?.type === "unrealized" && <span className="th-sort-indicator">{sortSpec.dir === "asc" ? "▲" : "▼"}</span>}
                </button>
                <ColumnResizeHandle onToggle={toggleContentFit} />
              </th>
              <th>
                <button type="button" className="th-sort" style={{ justifyContent: "flex-end" }} onClick={() => toggleSimpleSort("pnl")}>
                  Realized PNL
                  {sortSpec?.type === "pnl" && <span className="th-sort-indicator">{sortSpec.dir === "asc" ? "▲" : "▼"}</span>}
                </button>
                <ColumnResizeHandle onToggle={toggleContentFit} />
              </th>
              <th>
                <div className="th-inner" style={{ justifyContent: "flex-end" }}>
                  <span className="th-label">Current Price</span>
                </div>
                <ColumnResizeHandle onToggle={toggleContentFit} />
              </th>
              <th>
                <div className="th-inner" style={{ justifyContent: "flex-end" }}>
                  <span className="th-label">Avg Open Price</span>
                </div>
                <ColumnResizeHandle onToggle={toggleContentFit} />
              </th>
              <th>
                <button
                  type="button"
                  className="th-sort"
                  style={{ justifyContent: "flex-end" }}
                  onClick={() => toggleSimpleSort("dayOpen")}
                >
                  Day Open PNL
                  {sortSpec?.type === "dayOpen" && <span className="th-sort-indicator">{sortSpec.dir === "asc" ? "▲" : "▼"}</span>}
                </button>
                <ColumnResizeHandle onToggle={toggleContentFit} />
              </th>
            </tr>
          </thead>
          <tbody>
            <TotalRow
              node={totalsNode}
              onPriceChange={handlePriceChange}
              selectedKey={selectedKey}
              onSelect={setSelectedKey}
            />
            {displayRoot.children.map((account) => (
              <TreeRows
                key={account.key}
                node={account}
                expanded={expanded}
                toggle={toggle}
                onPriceChange={handlePriceChange}
                selectedKey={selectedKey}
                onSelect={toggleRowSelect}
              />
            ))}
            {displayRoot.children.length === 0 && (
              <tr>
                <td colSpan={14}>
                  <div className="empty-state">
                    <p className="status">No rows match the current filters.</p>
                    <button type="button" className="link-btn" onClick={() => setColumnFilters({})}>
                      Clear all filters
                    </button>
                  </div>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
