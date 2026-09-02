import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useClickOutside } from "../utils/useClickOutside.js";
import { ListFilter, ArrowUpAZ, ArrowDownAZ, Search } from "./icons.jsx";

const MENU_WIDTH = 220;
const MENU_MAX_HEIGHT = 360;

export default function ColumnFilterMenu({ values, selected, onChange, sortDir, onSort }) {
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

  useEffect(() => {
    if (!open) return;
    setDraft(selected ?? new Set(values));
    setSearch("");

    // Position the portaled menu against the trigger, clamped to the
    // viewport so it never gets clipped by an ancestor's overflow:auto (the
    // scrolling table container) or run off the edge of the screen.
    const rect = containerRef.current.getBoundingClientRect();
    let left = rect.right - MENU_WIDTH;
    left = Math.max(8, Math.min(left, window.innerWidth - MENU_WIDTH - 8));

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
            style={{ position: "fixed", top: menuPos.top, left: menuPos.left, right: "auto", width: MENU_WIDTH }}
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
              <input autoFocus placeholder="Search values..." value={search} onChange={(e) => setSearch(e.target.value)} />
            </div>

            <label className="col-filter-value col-filter-selectall">
              <input type="checkbox" checked={allVisibleChecked} onChange={toggleAllVisible} />
              (Select all)
            </label>

            <div className="col-filter-values">
              {visibleValues.map((v) => (
                <label key={v} className="col-filter-value">
                  <input type="checkbox" checked={draft.has(v)} onChange={() => toggleValue(v)} />
                  <span title={v}>{v === "" ? "(blank)" : v}</span>
                </label>
              ))}
              {visibleValues.length === 0 && <div className="col-filter-empty">No matches</div>}
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
