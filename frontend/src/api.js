// VITE_API_URL overrides this if set, but a hardcoded localhost URL baked
// into the built bundle would break every client except the machine running
// it — a device on the LAN loading this page needs API calls to go back to
// whatever host/IP it used to reach the page in the first place, not its
// own localhost. Falling back to window.location.hostname makes this work
// automatically whether accessed as localhost or a LAN IP.
//
// The port itself depends on dev vs. production (import.meta.env.DEV is
// Vite's own build-time flag — true only under `vite dev`, false in a
// production build) — local dev's backend runs on 8021 (see backend/.env's
// PORT), Docker's production backend always on 8020, matching vite.config.js's
// same dev(5191)/production(5190) split for the frontend itself.
const API_PORT = import.meta.env.DEV ? 8021 : 8020;
const BASE_URL = import.meta.env.VITE_API_URL || `http://${window.location.hostname}:${API_PORT}`;

// Same host resolution as BASE_URL, just with the ws(s):// scheme swapped in
// — used by useFillsSyncedSocket so a LAN client's socket goes back to the
// same host it loaded the page from, not its own localhost.
export const WS_BASE_URL = BASE_URL.replace(/^http/, "ws");

async function request(path, options = {}) {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });

  const text = await res.text();
  const body = text ? JSON.parse(text) : null;

  if (!res.ok) {
    const message = body?.detail ? JSON.stringify(body.detail) : res.statusText;
    throw new Error(message);
  }

  return body;
}

const get = (path) => request(path);
const post = (path, data) => request(path, { method: "POST", body: data ? JSON.stringify(data) : undefined });
const put = (path, data) => request(path, { method: "PUT", body: JSON.stringify(data) });
const del = (path) => request(path, { method: "DELETE" });

export const api = {
  // Accounts
  listAccounts: () => get("/api/accounts/"),
  createAccount: (name, pnlStartDate) => post("/api/accounts/", { name, pnl_start_date: pnlStartDate }),
  updateAccountPnlStartDate: (id, pnlStartDate) => put(`/api/accounts/${id}/pnl-start-date`, { pnl_start_date: pnlStartDate }),
  deleteAccount: (id) => del(`/api/accounts/${id}`),

  // Fills
  syncFillsForAccountDateRange: (accountName, startDate, endDate) =>
    post(
      `/api/fills/sync-account/${encodeURIComponent(accountName)}?start_date=${startDate}&end_date=${endDate}`,
    ),
  getLastSynced: () => get("/api/fills/last-synced"),

  // PNL
  getPnlOverview: () => get("/api/pnl/overview"),

  // UI state (shared globally — no per-user accounts in this app)
  getUiState: (key) => get(`/api/ui-state/${encodeURIComponent(key)}`),
  setUiState: (key, value) => put(`/api/ui-state/${encodeURIComponent(key)}`, { value }),
};
