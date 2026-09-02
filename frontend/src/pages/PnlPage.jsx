import { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";
import PnlTree from "../components/PnlTree.jsx";
import { useFillsSyncedSocket } from "../utils/useFillsSyncedSocket.js";

export default function PnlPage() {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);

  const fetchRows = useCallback(() => {
    api.getPnlOverview().then(setRows).catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    fetchRows();
  }, [fetchRows]);

  // Refetch the moment the backend's fill-sync scheduler pushes a
  // fills_synced update, instead of only on page load/reload.
  useFillsSyncedSocket(fetchRows);

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
