import { useEffect, useState } from "react";
import { api } from "../api.js";
import PnlTree from "../components/PnlTree.jsx";

export default function PnlPage() {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.getPnlOverview().then(setRows).catch((e) => setError(e.message));
  }, []);

  return (
    <div className="page-fill">
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      {rows && rows.length > 0 && <PnlTree rows={rows} />}

      {rows && rows.length === 0 && <p className="status">No PNL data yet — add an account and sync some fills first.</p>}
    </div>
  );
}
