import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useClickOutside } from "../utils/useClickOutside.js";
import { CalendarDays, ChevronLeft, ChevronRight } from "./icons.jsx";

const WEEKDAYS = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"];
const MONTH_NAMES = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

const PANEL_WIDTH = 240;
const PANEL_MAX_HEIGHT = 320;

function toDateString(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function parseDateString(s) {
  if (!s) return null;
  const [y, m, d] = s.split("-").map(Number);
  return new Date(y, m - 1, d);
}

function isSameDay(a, b) {
  return !!a && !!b && a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

function buildGrid(viewDate) {
  const year = viewDate.getFullYear();
  const month = viewDate.getMonth();
  const startOffset = new Date(year, month, 1).getDay();
  const gridStart = new Date(year, month, 1 - startOffset);

  return Array.from({ length: 42 }, (_, i) => {
    const d = new Date(gridStart);
    d.setDate(gridStart.getDate() + i);
    return d;
  });
}

export default function DatePickerField({ id, label, value, onChange }) {
  const [open, setOpen] = useState(false);
  const [viewDate, setViewDate] = useState(() => parseDateString(value) || new Date());
  const [panelPos, setPanelPos] = useState(null);

  const panelRef = useRef(null);
  // A portaled panel isn't a DOM descendant of the trigger, so it needs to
  // be treated as "inside" too — otherwise clicking inside it reads as an
  // outside click and the panel slams shut.
  const ref = useClickOutside(() => setOpen(false), panelRef);

  useEffect(() => {
    if (!open) return;
    setViewDate(parseDateString(value) || new Date());

    // Position the portaled panel against the trigger, clamped to the
    // viewport so it never gets clipped by an ancestor's overflow:auto (the
    // scrolling table container) or run off the edge of the screen.
    const rect = ref.current.getBoundingClientRect();
    let left = rect.left;
    left = Math.max(8, Math.min(left, window.innerWidth - PANEL_WIDTH - 8));

    let top = rect.bottom + 4;
    if (top + PANEL_MAX_HEIGHT > window.innerHeight) {
      top = Math.max(8, rect.top - PANEL_MAX_HEIGHT - 4);
    }

    setPanelPos({ top, left });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const selected = parseDateString(value);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const cells = buildGrid(viewDate);

  const selectDay = (d) => {
    onChange(toDateString(d));
    setOpen(false);
  };

  return (
    <div className="field" ref={ref} style={{ position: "relative" }}>
      <label htmlFor={id}>{label}</label>
      <button type="button" id={id} className="date-field-trigger" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <CalendarDays size={13} />
        {value || "Select date"}
      </button>

      {open &&
        panelPos &&
        createPortal(
          <div
            className="datepicker-panel"
            role="dialog"
            aria-label={`${label} calendar`}
            ref={panelRef}
            style={{ position: "fixed", top: panelPos.top, left: panelPos.left, width: PANEL_WIDTH }}
          >
            <div className="datepicker-header">
              <button
                type="button"
                className="datepicker-nav-btn"
                onClick={() => setViewDate(new Date(viewDate.getFullYear(), viewDate.getMonth() - 1, 1))}
                aria-label="Previous month"
              >
                <ChevronLeft size={14} />
              </button>
              <span>
                {MONTH_NAMES[viewDate.getMonth()]} {viewDate.getFullYear()}
              </span>
              <button
                type="button"
                className="datepicker-nav-btn"
                onClick={() => setViewDate(new Date(viewDate.getFullYear(), viewDate.getMonth() + 1, 1))}
                aria-label="Next month"
              >
                <ChevronRight size={14} />
              </button>
            </div>

            <div className="datepicker-weekdays">
              {WEEKDAYS.map((w) => (
                <span key={w}>{w}</span>
              ))}
            </div>

            <div className="datepicker-grid">
              {cells.map((d) => {
                const outside = d.getMonth() !== viewDate.getMonth();
                const classes = [
                  "datepicker-day",
                  outside && "outside",
                  isSameDay(d, selected) && "selected",
                  isSameDay(d, today) && "today",
                ]
                  .filter(Boolean)
                  .join(" ");
                return (
                  <button type="button" key={d.toISOString()} className={classes} onClick={() => selectDay(d)}>
                    {d.getDate()}
                  </button>
                );
              })}
            </div>

            <div className="datepicker-footer">
              <button type="button" className="link-btn" onClick={() => selectDay(today)}>
                Today
              </button>
            </div>
          </div>,
          document.body,
        )}
    </div>
  );
}
