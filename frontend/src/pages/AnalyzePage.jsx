import { forwardRef, lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api.js";
import DataTable, { ExportMenu, ColumnStatsBadge } from "../components/DataTable.jsx";
import { DataUploadButton } from "../components/DataUploadControl.jsx";
import { ChevronDown, ChevronUp, X } from "../components/icons.jsx";

// Plotly (see PnlHistogramChart.jsx) is a large dependency even in its
// "basic" build — lazy so only the Analyze page ever downloads it.
const PnlHistogramChart = lazy(() => import("../components/PnlHistogramChart.jsx"));

const MONEY_METRICS = new Set(["Total PnL", "Average PnL / Trade", "Average PnL / Lot", "Average Win", "Average Loss"]);

function signClass(n) {
  if (n > 0) return "pos";
  if (n < 0) return "neg";
  return "zero";
}

function formatMetricValue(metric, value) {
  if (value === null || value === undefined) return "—";
  if (metric === "Win Rate") return `${(value * 100).toFixed(2)}%`;
  if (MONEY_METRICS.has(metric)) return value.toFixed(2);
  return Number.isInteger(value) ? value.toLocaleString() : value.toFixed(2);
}

// Every plain numeric column on this page (counts, lots, tick ranges) —
// right-aligned, monospace, no sign coloring (these are never meaningfully
// negative).
function numCell(key, label, { decimals = 2, integer = false } = {}) {
  return {
    key,
    label,
    align: "right",
    render: (r) => {
      const v = r[key];
      if (v === null || v === undefined) return "—";
      return <span className="num-cell">{integer ? Number(v).toLocaleString() : Number(v).toFixed(decimals)}</span>;
    },
  };
}

// Every PnL-derived numeric column — same as numCell, plus red/green/gray
// by sign, same convention as the rest of the app's money figures.
function pnlCell(key, label) {
  return {
    key,
    label,
    align: "right",
    render: (r) => {
      const v = r[key];
      if (v === null || v === undefined) return "—";
      return <span className={`num-cell ${signClass(v)}`}>{v.toFixed(2)}</span>;
    },
  };
}

const TRADE_COLUMNS = [
  { key: "contract", label: "Contract" },
  { key: "entry_time", label: "Entry Time", sortValue: (r) => r.entry_time_ns, dateFilter: true },
  { key: "exit_time", label: "Exit Time", sortValue: (r) => r.exit_time_ns, dateFilter: true },
  numCell("lots", "Lots", { integer: true }),
  { key: "entry_side", label: "Entry Side" },
  numCell("entry_price", "Entry Price", { decimals: 4 }),
  numCell("exit_price", "Exit Price", { decimals: 4 }),
  pnlCell("pnl", "PnL"),
  numCell("duration_ms", "Duration (ms)", { integer: true }),
  numCell("exit_fill_count", "Exit Fill Count", { integer: true }),
  numCell("gdu_lots_traded", "GDU Lots Traded", { integer: true }),
  numCell("gc_10s_range", "GC 10 Sec Range (Ticks)"),
  numCell("gc_1m_range", "GC 1 Min Range (Ticks)"),
  numCell("gc_5m_range", "GC 5 Min Range (Ticks)"),
  numCell("gc_30m_range", "GC 30 Min Range (Ticks)"),
];

const GDU_ANALYSIS_COLUMNS = [
  numCell("gdu_lots_traded", "Lots", { integer: true }),
  numCell("trades", "Trades", { integer: true }),
  numCell("total_trade_lots", "Total Lots", { integer: true }),
  pnlCell("total_pnl", "Total PnL"),
  pnlCell("average_pnl_per_lot", "Avg/Lot"),
  pnlCell("median_pnl", "Median"),
  numCell("avg_gc_10s_range", "GC 10 Sec"),
  numCell("avg_gc_1m_range", "GC 1 Min"),
  numCell("avg_gc_5m_range", "GC 5 Min"),
  numCell("avg_gc_30m_range", "GC 30 Min"),
];

const HALF_HOUR_COLUMNS = [
  { key: "half_hour_start", label: "Start" },
  { key: "half_hour_end", label: "End" },
  pnlCell("pnl", "PnL"),
  numCell("number_of_trades", "Trades", { integer: true }),
  numCell("lots", "Lots", { integer: true }),
  pnlCell("average_pnl_per_lot", "Avg/Lot"),
  numCell("avg_gc_10s_range", "GC 10s"),
  numCell("avg_gc_1m_range", "GC 1m"),
  numCell("avg_gc_5m_range", "GC 5m"),
  numCell("avg_gc_30m_range", "GC 30m"),
];

const HALF_HOURLY_SUMMARY_COLUMNS = [
  { key: "time_bucket", label: "Time Bucket" },
  pnlCell("pnl", "PnL"),
  numCell("number_of_trades", "Trades", { integer: true }),
  numCell("lots", "Lots", { integer: true }),
  pnlCell("average_pnl_per_lot", "Avg/Lot"),
  numCell("avg_gc_10s_range", "GC 10s"),
  numCell("avg_gc_1m_range", "GC 1m"),
  numCell("avg_gc_5m_range", "GC 5m"),
  numCell("avg_gc_30m_range", "GC 30m"),
];

const Section = forwardRef(function Section(
  { title, meta, headerExtra, bodyClassName, className, style, defaultCollapsed = false, children },
  ref
) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  return (
    <section
      ref={ref}
      style={style}
      className={`analyze-section${collapsed ? " analyze-section--collapsed" : ""}${className ? ` ${className}` : ""}`}
    >
      <div className="analyze-section-header">
        <button
          type="button"
          className="analyze-section-collapse-btn"
          onClick={() => setCollapsed((c) => !c)}
          aria-expanded={!collapsed}
          aria-label={collapsed ? `Expand ${title}` : `Collapse ${title}`}
          title={collapsed ? "Expand" : "Collapse"}
        >
          {collapsed ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
        </button>
        <h3 className="analyze-section-title">{title}</h3>
        {meta && <span className="analyze-section-meta">{meta}</span>}
        {headerExtra}
      </div>
      {!collapsed && <div className={`analyze-section-body${bodyClassName ? ` ${bodyClassName}` : ""}`}>{children}</div>}
    </section>
  );
});

const TRADE_FILTERS = [
  { key: "all", label: "All Trades", filenameSuffix: "trades" },
  { key: "profit", label: "Profitable", filenameSuffix: "profitable_trades" },
  { key: "loss", label: "Loss Making", filenameSuffix: "loss_making_trades" },
  { key: "scratch", label: "Scratched", filenameSuffix: "scratched_trades" },
];

export default function AnalyzePage({ upload }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [tradeStats, setTradeStats] = useState(null);
  const [tradesVisible, setTradesVisible] = useState(false);
  const [tradeFilter, setTradeFilter] = useState("all");
  const gduSectionRef = useRef(null);
  const hhSummarySectionRef = useRef(null);
  const [rowHeights, setRowHeights] = useState({ row1: null, row2: null });

  useEffect(() => {
    api.getGduFifoAnalysis().then(setData).catch((e) => setError(e.message));
  }, []);

  // Half Hour and PnL Histogram don't have a natural height of their own —
  // they should just fill whatever height GDU Analysis / Half Hourly Summary
  // (their row-mates) settle on, then Half Hour scrolls internally instead
  // of growing the row (see .analyze-quad-scroll-body). A plain CSS grid
  // 'auto' row can't do this on its own: both cells' full unclipped content
  // height counts toward the row's size, so the row would just grow to fit
  // whichever table has more rows. Watching the reference cell's rendered
  // height directly and mirroring it onto the other cell sidesteps that.
  useEffect(() => {
    if (!data) return;
    const targets = [gduSectionRef.current, hhSummarySectionRef.current].filter(Boolean);
    if (targets.length === 0) return;
    const observer = new ResizeObserver((entries) => {
      setRowHeights((prev) => {
        const next = { ...prev };
        for (const entry of entries) {
          const height = entry.borderBoxSize?.[0]?.blockSize ?? entry.contentRect.height;
          if (entry.target === gduSectionRef.current) next.row1 = height;
          if (entry.target === hhSummarySectionRef.current) next.row2 = height;
        }
        return next;
      });
    });
    targets.forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, [data]);

  const summaryColumns = useMemo(() => {
    if (!data) return [];
    return data.summary.map((m) => ({
      key: m.metric,
      label: m.metric,
      align: "right",
      render: (r) => (
        <span className={`num-cell${MONEY_METRICS.has(m.metric) && r[m.metric] !== null ? ` ${signClass(r[m.metric])}` : ""}`}>
          {formatMetricValue(m.metric, r[m.metric])}
        </span>
      ),
    }));
  }, [data]);

  const summaryRows = useMemo(() => {
    if (!data) return [];
    return [Object.fromEntries(data.summary.map((m) => [m.metric, m.value]))];
  }, [data]);

  const tradeCounts = useMemo(() => {
    const counts = { all: 0, profit: 0, loss: 0, scratch: 0 };
    if (!data) return counts;
    for (const t of data.trades) {
      counts.all++;
      if (t.pnl > 0) counts.profit++;
      else if (t.pnl < 0) counts.loss++;
      else counts.scratch++;
    }
    return counts;
  }, [data]);

  const filteredTrades = useMemo(() => {
    if (!data) return [];
    if (tradeFilter === "profit") return data.trades.filter((t) => t.pnl > 0);
    if (tradeFilter === "loss") return data.trades.filter((t) => t.pnl < 0);
    if (tradeFilter === "scratch") return data.trades.filter((t) => t.pnl === 0);
    return data.trades;
  }, [data, tradeFilter]);

  if (tradesVisible && data) {
    return (
      <div className="analyze-page">
        <div className="panel analyze-trades-panel analyze-trades-panel--expanded">
          <div className="row" style={{ marginBottom: "var(--space-3)" }}>
            <h3 className="panel-title" style={{ margin: 0 }}>
              Trade Sheet
            </h3>
            <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", marginLeft: "auto" }}>
              <ColumnStatsBadge stats={tradeStats} />
              <ExportMenu
                rows={filteredTrades}
                columns={TRADE_COLUMNS}
                filename={`${(data.contracts || []).join("_") || "product"}_${
                  TRADE_FILTERS.find((f) => f.key === tradeFilter)?.filenameSuffix || "trades"
                }`}
              />
              <button
                type="button"
                className="btn secondary xs icon-only"
                onClick={() => setTradesVisible(false)}
                aria-label="Hide trade sheet"
                title="Hide trade sheet"
              >
                <X size={15} />
              </button>
            </div>
          </div>
          <div className="trade-filter-tabs" role="tablist">
            {TRADE_FILTERS.map((f) => (
              <button
                key={f.key}
                type="button"
                role="tab"
                aria-selected={tradeFilter === f.key}
                className={`trade-filter-tab${tradeFilter === f.key ? " active" : ""}`}
                onClick={() => setTradeFilter(f.key)}
              >
                {f.label}
                <span className="trade-filter-tab-count">{tradeCounts[f.key].toLocaleString()}</span>
              </button>
            ))}
          </div>
          <DataTable rows={filteredTrades} columns={TRADE_COLUMNS} onColumnStatsChange={setTradeStats} />
        </div>
      </div>
    );
  }

  return (
    <div className="analyze-page">
      <div className="page-header">
        <h2>Analyze</h2>
        <div className="analyze-header-actions">
          <button type="button" className="btn secondary xs" onClick={() => setTradesVisible(true)}>
            View Trades
          </button>
          <DataUploadButton upload={upload} />
        </div>
      </div>

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      {data && (
        <div className="analyze-sections">
          <DataTable rows={summaryRows} columns={summaryColumns} compact showFilters={false} />

          <div className="analyze-quad-grid">
            <Section
              ref={gduSectionRef}
              title="GDU Analysis"
              meta={`${data.gdu_analysis.length} buckets`}
              className="analyze-quad-gdu"
            >
              <DataTable rows={data.gdu_analysis} columns={GDU_ANALYSIS_COLUMNS} />
            </Section>

            <Section
              title="PnL Histogram"
              meta={`${data.trades.length} trades`}
              bodyClassName="analyze-histogram-body"
              className="analyze-quad-hist"
              style={rowHeights.row1 ? { height: rowHeights.row1 } : undefined}
            >
              <Suspense fallback={<p className="status">Loading chart…</p>}>
                <PnlHistogramChart buckets={data.pnl_histogram} />
              </Suspense>
            </Section>

            <Section
              title="Half Hour"
              meta={`${data.half_hour.length} windows`}
              className="analyze-quad-halfhour"
              style={rowHeights.row2 ? { height: rowHeights.row2 } : undefined}
            >
              <DataTable rows={data.half_hour} columns={HALF_HOUR_COLUMNS} />
            </Section>

            <Section
              ref={hhSummarySectionRef}
              title="Half Hourly Summary"
              meta={`${data.half_hourly_summary.length} slots`}
              className="analyze-quad-hhsummary"
            >
              <DataTable rows={data.half_hourly_summary} columns={HALF_HOURLY_SUMMARY_COLUMNS} />
            </Section>
          </div>
        </div>
      )}
    </div>
  );
}
