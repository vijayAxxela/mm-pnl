import { NavLink, Route, Routes, Navigate } from "react-router-dom";
import AccountsPage from "./pages/AccountsPage.jsx";
import PnlPage from "./pages/PnlPage.jsx";
import FillsPage from "./pages/FillsPage.jsx";
import LastSyncedBadge from "./components/LastSyncedBadge.jsx";

const links = [
  { to: "/pnl", label: "PNL" },
  { to: "/fills", label: "Fills" },
  { to: "/accounts", label: "Accounts" },
];

export default function App() {
  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-brand">
          <span className="dot" aria-hidden="true" />
          MM PNL
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
      <main className="main">
        <Routes>
          <Route path="/" element={<Navigate to="/pnl" replace />} />
          <Route path="/accounts" element={<AccountsPage />} />
          <Route path="/fills" element={<FillsPage />} />
          <Route path="/pnl" element={<PnlPage />} />
        </Routes>
      </main>
    </div>
  );
}
