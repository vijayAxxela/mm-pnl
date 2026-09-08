import { useMemo } from "react";
import createPlotlyComponent from "react-plotly.js/factory";
// The "basic" dist (not the full ~3MB plotly.js) — bar/pie/scatter/box
// traces only, which covers every chart this app needs so far. Bucket
// counts are already computed server-side (see routes/analyze.py's
// _build_pnl_histogram), so this renders a plain bar trace rather than
// letting Plotly's own "histogram" trace re-bin raw values.
import Plotly from "plotly.js-basic-dist-min";

const Plot = createPlotlyComponent(Plotly);

const COLOR = {
  loss: "#f0475a",
  profit: "#22c55e",
  mixed: "#7c88a1",
};

export default function PnlHistogramChart({ buckets }) {
  const { x, y, colors, text } = useMemo(() => {
    return {
      x: buckets.map((b) => b.label),
      y: buckets.map((b) => b.count),
      colors: buckets.map((b) => COLOR[b.color] || COLOR.mixed),
      text: buckets.map((b) => (b.count > 0 ? String(b.count) : "")),
    };
  }, [buckets]);

  if (!buckets || buckets.length === 0) {
    return <p className="status">No trades to plot yet.</p>;
  }

  return (
    <Plot
      data={[
        {
          type: "bar",
          x,
          y,
          text,
          textposition: "outside",
          textfont: { color: "#7c88a1", size: 10, family: "Fira Code, ui-monospace, monospace" },
          marker: { color: colors },
          hovertemplate: "%{x}<br>%{y} trade(s)<extra></extra>",
        },
      ]}
      layout={{
        autosize: true,
        margin: { l: 44, r: 16, t: 16, b: 60 },
        paper_bgcolor: "transparent",
        plot_bgcolor: "transparent",
        font: { color: "#e5e9f0", size: 11, family: "Fira Sans, system-ui, sans-serif" },
        xaxis: {
          title: { text: "PnL bucket", font: { size: 11 } },
          tickangle: -40,
          gridcolor: "rgba(255,255,255,0.06)",
          linecolor: "#232a3d",
          automargin: true,
        },
        yaxis: {
          title: { text: "Trades", font: { size: 11 } },
          gridcolor: "rgba(255,255,255,0.06)",
          linecolor: "#232a3d",
          zerolinecolor: "#333c54",
        },
        bargap: 0.15,
        showlegend: false,
      }}
      config={{
        displaylogo: false,
        responsive: true,
        toImageButtonOptions: { format: "png", filename: "pnl_histogram" },
        modeBarButtonsToRemove: ["lasso2d", "select2d"],
      }}
      style={{ width: "100%", height: "100%" }}
      useResizeHandler
    />
  );
}
