import { useState } from "react";
import { NavLink, Route, Routes, Navigate } from "react-router-dom";
import AccountsPage from "./pages/AccountsPage.jsx";
import PnlPage from "./pages/PnlPage.jsx";
import FillsPage from "./pages/FillsPage.jsx";
import AnalyzePage from "./pages/AnalyzePage.jsx";
import LastSyncedBadge from "./components/LastSyncedBadge.jsx";
import { DataUploadModal, DataUploadBanner } from "./components/DataUploadControl.jsx";
import { useLossAlertSound } from "./utils/useLossAlertSound.js";
import { useDataUpload } from "./utils/useDataUpload.js";

const links = [
  { to: "/pnl", label: "PNL" },
  { to: "/fills", label: "Fills" },
  { to: "/accounts", label: "Accounts" },
  { to: "/analyze", label: "Analyze" },
];

export default function App() {
  const [lossToast, setLossToast] = useState(null);
  const upload = useDataUpload();

  // Global — fires regardless of which page is open, since a combined-loss
  // alert matters no matter what the user's currently looking at.
  useLossAlertSound((message) => {
    setLossToast(message);
    setTimeout(() => setLossToast(null), 15000);
  });

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-brand">
          <span className="dot" aria-hidden="true" />
          MM PNL
          {import.meta.env.DEV && (
            <span className="dev-badge" title="Local development build">
              DEV
            </span>
          )}
        </div>
        <nav className="topbar-nav">
          {links.map((link) => (
            <NavLink key={link.to} to={link.to} className={({ isActive }) => (isActive ? "active" : "")}>
              {link.label}
            </NavLink>
          ))}
        </nav>
        <LastSyncedBadge />
      </header>

      <DataUploadModal upload={upload} />
      <DataUploadBanner upload={upload} />

      {lossToast && (
        <div className="loss-toast" role="alert">
          <span>
            Combined loss since day open has reached <strong>{lossToast.threshold.toFixed(2)}</strong> (currently{" "}
            {lossToast.loss.toFixed(2)})
          </span>
          <button type="button" className="loss-toast-dismiss" onClick={() => setLossToast(null)} aria-label="Dismiss">
            ×
          </button>
        </div>
      )}

      <main className="main">
        <Routes>
          <Route path="/" element={<Navigate to="/pnl" replace />} />
          <Route path="/accounts" element={<AccountsPage />} />
          <Route path="/fills" element={<FillsPage />} />
          <Route path="/pnl" element={<PnlPage />} />
          <Route path="/analyze" element={<AnalyzePage upload={upload} />} />
        </Routes>
      </main>
    </div>
  );
}
