import { useMemo, useState } from "react";

// Matches DataTable's date/time display convention (see AnalyzePage.jsx's
// entry_time/exit_time, formatted server-side as "DD-MM-YY HH:MM:SS.mmm").
// Values that don't match this shape are simply left out of the tree —
// this component is only ever used for columns known to be that format.
const TIMESTAMP_RE = /^(\d{2})-(\d{2})-(\d{2}) (\d{2}:\d{2}:\d{2}\.\d{3})$/;

const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

// Year > Month > Day > exact time — an Excel-style "Date Filters" tree, built
// straight from the column's own unique display strings (no separate date
// parsing/timezone logic needed — the string IS already IST-formatted text).
function buildDateTree(values) {
  const years = new Map(); // year(number) -> Map(month(number) -> Map(day(string) -> [{time, fullValue}]))

  for (const v of values) {
    const m = TIMESTAMP_RE.exec(v);
    if (!m) continue;
    const [, dd, mm, yy, time] = m;
    const year = 2000 + Number(yy);
    const month = Number(mm);

    if (!years.has(year)) years.set(year, new Map());
    const months = years.get(year);
    if (!months.has(month)) months.set(month, new Map());
    const days = months.get(month);
    if (!days.has(dd)) days.set(dd, []);
    days.get(dd).push({ time, fullValue: v });
  }

  return [...years.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([year, months]) => ({
      key: String(year),
      label: String(year),
      children: [...months.entries()]
        .sort((a, b) => a[0] - b[0])
        .map(([month, days]) => ({
          key: `${year}-${month}`,
          label: MONTH_NAMES[month - 1] || String(month),
          children: [...days.entries()]
            .sort((a, b) => a[0].localeCompare(b[0]))
            .map(([day, times]) => ({
              key: `${year}-${month}-${day}`,
              label: day,
              leaves: [...times].sort((a, b) => a.time.localeCompare(b.time)),
            })),
        })),
    }));
}

function collectValues(node) {
  if (node.leaves) return node.leaves.map((l) => l.fullValue);
  return node.children.flatMap(collectValues);
}

function TreeBranch({ node, draft, onToggleMany, depth }) {
  const isDay = !!node.leaves;
  const childCount = isDay ? node.leaves.length : node.children.length;
  // Auto-expand a chain with only one option at this level (exactly what
  // Excel does — a single year/month expands itself, a day with several
  // distinct times does not), so a small dataset needs no clicking at all.
  const [expanded, setExpanded] = useState(childCount === 1);

  const allValues = useMemo(() => collectValues(node), [node]);
  const checkedCount = allValues.filter((v) => draft.has(v)).length;
  const state = checkedCount === 0 ? "none" : checkedCount === allValues.length ? "all" : "some";

  return (
    <div>
      <label className="col-filter-value date-tree-row" style={{ paddingLeft: 4 + depth * 16 }}>
        <button
          type="button"
          className="date-tree-toggle"
          onClick={(e) => {
            e.preventDefault();
            setExpanded((v) => !v);
          }}
          aria-label={expanded ? "Collapse" : "Expand"}
        >
          {expanded ? "▾" : "▸"}
        </button>
        <input
          type="checkbox"
          checked={state === "all"}
          ref={(el) => {
            if (el) el.indeterminate = state === "some";
          }}
          onChange={() => onToggleMany(allValues, state !== "all")}
        />
        <span title={node.label}>{node.label}</span>
      </label>

      {expanded &&
        (isDay
          ? node.leaves.map((leaf) => (
              <label key={leaf.fullValue} className="col-filter-value" style={{ paddingLeft: 4 + (depth + 1) * 16 }}>
                <input
                  type="checkbox"
                  checked={draft.has(leaf.fullValue)}
                  onChange={() => onToggleMany([leaf.fullValue], !draft.has(leaf.fullValue))}
                />
                <span title={leaf.time}>{leaf.time}</span>
              </label>
            ))
          : node.children.map((child) => (
              <TreeBranch key={child.key} node={child} draft={draft} onToggleMany={onToggleMany} depth={depth + 1} />
            )))}
    </div>
  );
}

export default function DateFilterTree({ values, draft, onToggleMany }) {
  const tree = useMemo(() => buildDateTree(values), [values]);

  if (tree.length === 0) return <div className="col-filter-empty">No matches</div>;

  return (
    <div className="date-tree">
      {tree.map((node) => (
        <TreeBranch key={node.key} node={node} draft={draft} onToggleMany={onToggleMany} depth={0} />
      ))}
    </div>
  );
}
