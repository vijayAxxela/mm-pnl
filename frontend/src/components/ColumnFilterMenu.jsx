import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useClickOutside } from "../utils/useClickOutside.js";
import { ListFilter, ArrowUpAZ, ArrowDownAZ, Search } from "./icons.jsx";
import DateFilterTree from "./DateFilterTree.jsx";

const MENU_WIDTH = 220;
const DATE_MENU_WIDTH = 250;
const MENU_MAX_HEIGHT = 360;

// dateMode: true renders values (expected to be the "DD-MM-YY
// HH:MM:SS.mmm" strings DataTable's date columns produce — see
// AnalyzePage.jsx's entry_time/exit_time) as an Excel-style Year > Month >
// Day > Time checkbox tree instead of a flat list, while still typing into
// the exact same draft Set<string> everything else here (Select all,
// Apply/Clear, the underlying columnFilters state) already works with.
export default function ColumnFilterMenu({ values, selected, onChange, sortDir, onSort, dateMode = false }) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [draft, setDraft] = useState(selected ?? new Set(values));
  const [menuPos, setMenuPos] = useState(null);

  const menuRef = useRef(null);
  // A portaled menu isn't a DOM descendant of the trigger, so it needs to be
  // treated as "inside" too — otherwise clicking inside it reads as an
  // outside click and the menu slams shut.
  const containerRef = useClickOutside(() => setOpen(false), menuRef);

  const active = selected !== null;
  const menuWidth = dateMode ? DATE_MENU_WIDTH : MENU_WIDTH;
  // The tree is only shown while not searching — typing a search still
  // falls back to the flat matching list (same as Excel itself does).
  const showTree = dateMode && search.trim() === "";

  useEffect(() => {
    if (!open) return;
    setDraft(selected ?? new Set(values));
    setSearch("");

    // Position the portaled menu against the trigger, clamped to the
    // viewport so it never gets clipped by an ancestor's overflow:auto (the
    // scrolling table container) or run off the edge of the screen.
    const rect = containerRef.current.getBoundingClientRect();
    let left = rect.right - menuWidth;
    left = Math.max(8, Math.min(left, window.innerWidth - menuWidth - 8));

    let top = rect.bottom + 4;
    if (top + MENU_MAX_HEIGHT > window.innerHeight) {
      top = Math.max(8, rect.top - MENU_MAX_HEIGHT - 4);
    }

    setMenuPos({ top, left });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const visibleValues = values.filter((v) => v.toLowerCase().includes(search.trim().toLowerCase()));
  const allVisibleChecked = visibleValues.length > 0 && visibleValues.every((v) => draft.has(v));

  const toggleValue = (v) => {
    setDraft((prev) => {
      const next = new Set(prev);
      if (next.has(v)) next.delete(v);
      else next.add(v);
      return next;
    });
  };

  const toggleAllVisible = () => {
    setDraft((prev) => {
      const next = new Set(prev);
      if (allVisibleChecked) visibleValues.forEach((v) => next.delete(v));
      else visibleValues.forEach((v) => next.add(v));
      return next;
    });
  };

  // Bulk toggle for the date tree — a Year/Month/Day node checks/unchecks
  // every exact timestamp underneath it in one go.
  const toggleMany = (vals, checked) => {
    setDraft((prev) => {
      const next = new Set(prev);
      for (const v of vals) {
        if (checked) next.add(v);
        else next.delete(v);
      }
      return next;
    });
  };

  const apply = () => {
    const isEverything = values.length > 0 && values.every((v) => draft.has(v));
    onChange(isEverything ? null : new Set(draft));
    setOpen(false);
  };

  const clear = () => {
    onChange(null);
    setOpen(false);
  };

  return (
    <div className="col-filter" ref={containerRef}>
      <button
        type="button"
        className={`col-filter-trigger${active ? " active" : ""}`}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-label="Sort and filter column"
      >
        <ListFilter size={12} />
      </button>

      {open &&
        menuPos &&
        createPortal(
          <div
            className="col-filter-menu"
            role="menu"
            ref={menuRef}
            style={{ position: "fixed", top: menuPos.top, left: menuPos.left, right: "auto", width: menuWidth }}
          >
            <button
              type="button"
              className={`col-filter-sort-item${sortDir === "asc" ? " active" : ""}`}
              onClick={() => {
                onSort("asc");
                setOpen(false);
              }}
            >
              <ArrowUpAZ size={13} />
              Sort ascending
            </button>
            <button
              type="button"
              className={`col-filter-sort-item${sortDir === "desc" ? " active" : ""}`}
              onClick={() => {
                onSort("desc");
                setOpen(false);
              }}
            >
              <ArrowDownAZ size={13} />
              Sort descending
            </button>

            <div className="col-filter-divider" />

            <div className="col-filter-search">
              <Search size={12} className="col-filter-search-icon" aria-hidden="true" />
              <input
                autoFocus
                placeholder={dateMode ? "Search dates/times..." : "Search values..."}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>

            <label className="col-filter-value col-filter-selectall">
              <input type="checkbox" checked={allVisibleChecked} onChange={toggleAllVisible} />
              (Select all)
            </label>

            <div className="col-filter-values">
              {showTree ? (
                <DateFilterTree values={values} draft={draft} onToggleMany={toggleMany} />
              ) : (
                <>
                  {visibleValues.map((v) => (
                    <label key={v} className="col-filter-value">
                      <input type="checkbox" checked={draft.has(v)} onChange={() => toggleValue(v)} />
                      <span title={v}>{v === "" ? "(blank)" : v}</span>
                    </label>
                  ))}
                  {visibleValues.length === 0 && <div className="col-filter-empty">No matches</div>}
                </>
              )}
            </div>

            <div className="col-filter-footer">
              <button type="button" className="link-btn" onClick={clear}>
                Clear
              </button>
              <div className="col-filter-footer-actions">
                <button type="button" className="btn secondary xs" onClick={() => setOpen(false)}>
                  Cancel
                </button>
                <button type="button" className="btn xs" onClick={apply}>
                  OK
                </button>
              </div>
            </div>
          </div>,
          document.body,
        )}
    </div>
  );
}
