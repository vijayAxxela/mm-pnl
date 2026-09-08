import { Upload, X, Loader2 } from "./icons.jsx";

// Three small presentational pieces sharing one useDataUpload() instance
// (owned by App.jsx) instead of one component owning its own state — the
// trigger button lives in the topbar's flex row, but the progress banner
// needs to be a sibling of <main> so it pushes page content down in normal
// document flow (like the loss-toast) rather than overlaying it, which is
// only possible if App.jsx positions them independently in its own JSX.

export function DataUploadButton({ upload }) {
  return (
    <button type="button" className="btn secondary xs" onClick={upload.openModal} title="Add new Time & Sales / OHLC data">
      <Upload size={13} />
      Add New Data
    </button>
  );
}

export function DataUploadModal({ upload }) {
  if (!upload.modalOpen) return null;

  const { step, setStep, tsFile, setTsFile, ohlcFile, setOhlcFile, ohlcContract, setOhlcContract, submitting, formError, closeModal, handleSubmit } = upload;

  return (
    <div className="modal-overlay" onClick={closeModal}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3>Add New Data</h3>
          <button type="button" className="modal-close" onClick={closeModal} aria-label="Close" disabled={submitting}>
            <X size={16} />
          </button>
        </div>

        <div className="modal-steps">
          <span className={`modal-step${step === 1 ? " active" : ""}`}>1. GDU Time &amp; Sales</span>
          <span className={`modal-step${step === 2 ? " active" : ""}`}>2. GC OHLC</span>
        </div>

        {step === 1 && (
          <div className="modal-body">
            <p className="modal-hint">Upload the GDU Time &amp; Sales file (csv or xlsx). Optional — you can skip to step 2.</p>
            <input type="file" accept=".csv,.xlsx,.xls" onChange={(e) => setTsFile(e.target.files?.[0] || null)} />
            {tsFile && <p className="status">Selected: {tsFile.name}</p>}
          </div>
        )}

        {step === 2 && (
          <div className="modal-body">
            <p className="modal-hint">Upload the GC OHLC file (csv or xlsx). Optional — you can go back and add only Time &amp; Sales.</p>
            <input type="file" accept=".csv,.xlsx,.xls" onChange={(e) => setOhlcFile(e.target.files?.[0] || null)} />
            {ohlcFile && <p className="status">Selected: {ohlcFile.name}</p>}
            <div className="field" style={{ marginTop: "var(--space-3)" }}>
              <label htmlFor="ohlc-contract-input">Contract (only needed for a csv with no Contract column)</label>
              <input
                id="ohlc-contract-input"
                type="text"
                placeholder="e.g. GC Dec26"
                value={ohlcContract}
                onChange={(e) => setOhlcContract(e.target.value)}
              />
            </div>
          </div>
        )}

        {formError && (
          <p className="error" style={{ padding: "0 var(--space-5)" }}>
            {formError}
          </p>
        )}

        <div className="modal-footer">
          {step === 2 ? (
            <button type="button" className="btn secondary xs" onClick={() => setStep(1)} disabled={submitting}>
              Back
            </button>
          ) : (
            <span />
          )}
          {step === 1 ? (
            <button type="button" className="btn xs" onClick={() => setStep(2)}>
              Next
            </button>
          ) : (
            <button type="button" className="btn xs" onClick={handleSubmit} disabled={submitting}>
              {submitting && <Loader2 size={12} className="spin" />}
              {submitting ? "Uploading..." : "Upload & Save"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function summaryLine(summary) {
  if (!summary) return null;
  const parts = [];
  if (summary.time_and_sales) {
    const s = summary.time_and_sales;
    parts.push(`Time & Sales: +${s.inserted} new, ${s.skipped} already present`);
  }
  if (summary.ohlc_bars) {
    const s = summary.ohlc_bars;
    parts.push(`OHLC: +${s.inserted} new, ${s.skipped} already present`);
  }
  return parts.join(" · ");
}

export function DataUploadBanner({ upload }) {
  const { progress, dismissProgress } = upload;
  if (!progress) return null;

  return (
    <div className={`data-upload-banner${progress.error ? " error" : progress.done ? " done" : ""}`} role="status">
      <span className="data-upload-banner-message">
        {progress.error ? `Upload failed: ${progress.error}` : progress.done ? summaryLine(progress.summary) || progress.message : progress.message}
      </span>
      <div className="data-upload-progress-track">
        <div className="data-upload-progress-fill" style={{ width: `${Math.min(100, Math.max(0, progress.percent || 0))}%` }} />
      </div>
      {progress.done ? (
        <button type="button" className="data-upload-banner-dismiss" onClick={dismissProgress} aria-label="Dismiss">
          ×
        </button>
      ) : (
        <span className="data-upload-banner-hint">Don't refresh — you can switch pages while this runs.</span>
      )}
    </div>
  );
}
