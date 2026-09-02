import { useEffect, useState } from "react";
import { api } from "../api.js";

const POLL_MS = 60_000;

function formatIst(isoString) {
  if (!isoString) return null;
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Kolkata",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(isoString));
}

export default function LastSyncedBadge() {
  const [lastSyncedAt, setLastSyncedAt] = useState(null);

  useEffect(() => {
    const load = () => {
      api
        .getLastSynced()
        .then((r) => setLastSyncedAt(r.last_synced_at))
        .catch(() => {});
    };
    load();
    const interval = setInterval(load, POLL_MS);
    return () => clearInterval(interval);
  }, []);

  const formatted = formatIst(lastSyncedAt);

  return <span className="last-synced-badge">{formatted ? `Last synced: ${formatted}` : "Not synced yet"}</span>;
}
