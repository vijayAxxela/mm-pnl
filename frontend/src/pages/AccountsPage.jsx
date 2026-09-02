import { useEffect, useState } from "react";
import { api } from "../api.js";
import DatePickerField from "../components/DatePickerField.jsx";
import { Trash2, Loader2, Plus } from "../components/icons.jsx";

function todayIST() {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const get = (type) => parts.find((p) => p.type === type).value;
  return `${get("year")}-${get("month")}-${get("day")}`;
}

export default function AccountsPage() {
  const [accounts, setAccounts] = useState([]);
  const [name, setName] = useState("");
  const [pnlStartDate, setPnlStartDate] = useState(todayIST());
  const [error, setError] = useState(null);
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);
  const [pendingDeleteId, setPendingDeleteId] = useState(null);
  const [editingPnlStartId, setEditingPnlStartId] = useState(null);
  const [editingPnlStartValue, setEditingPnlStartValue] = useState("");
  const [pnlStartBusy, setPnlStartBusy] = useState(false);

  const loadAccounts = () => {
    api
      .listAccounts()
      .then(setAccounts)
      .catch((e) => setError(e.message));
  };

  useEffect(loadAccounts, []);

  const handleCreate = async (e) => {
    e.preventDefault();
    if (!name.trim() || !pnlStartDate) return;
    setError(null);
    setStatus(null);
    setBusy(true);
    try {
      const account = await api.createAccount(name.trim(), pnlStartDate);
      setStatus(`Added '${account.name}' (TT id ${account.tt_account_id}) — PNL tracked from ${pnlStartDate}`);
      setName("");
      loadAccounts();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (id) => {
    setError(null);
    setStatus(null);
    try {
      await api.deleteAccount(id);
      setPendingDeleteId(null);
      loadAccounts();
    } catch (e) {
      setError(e.message);
    }
  };

  const startEditPnlStart = (a) => {
    setError(null);
    setStatus(null);
    setEditingPnlStartId(a.id);
    setEditingPnlStartValue(a.pnl_start_date || todayIST());
  };

  const savePnlStart = async (a) => {
    setError(null);
    setStatus(null);
    setPnlStartBusy(true);
    try {
      await api.updateAccountPnlStartDate(a.id, editingPnlStartValue);
      setStatus(`Updated PNL start date for '${a.name}' to ${editingPnlStartValue}`);
      setEditingPnlStartId(null);
      loadAccounts();
    } catch (e) {
      setError(e.message);
    } finally {
      setPnlStartBusy(false);
    }
  };

  return (
    <div>
      <div className="page-header">
        <h2>Accounts</h2>
        <span className="status">{accounts.length} account{accounts.length === 1 ? "" : "s"}</span>
      </div>

      <div className="panel">
        <form className="row" onSubmit={handleCreate}>
          <div className="field">
            <label htmlFor="new-account-name">Account name</label>
            <input
              id="new-account-name"
              placeholder="e.g. EE093"
              value={name}
              onChange={(e) => setName(e.target.value)}
              size={10}
              style={{ width: "auto", minWidth: "10ch" }}
            />
          </div>
          <DatePickerField id="new-account-pnl-start" label="PNL start date" value={pnlStartDate} onChange={setPnlStartDate} />
          <button className="btn" type="submit" disabled={busy}>
            {busy ? <Loader2 size={13} className="spin" /> : <Plus size={13} />}
            {busy ? "Adding..." : "Add account"}
          </button>
          {error && (
            <span className="error" style={{ margin: 0 }}>
              {error}
            </span>
          )}
          {status && (
            <span className="status" style={{ margin: 0 }}>
              {status}
            </span>
          )}
        </form>
      </div>

      <div className="table-wrap" style={{ maxHeight: "none", width: "fit-content" }}>
        <table className="table-loose" style={{ width: "auto" }}>
          <thead>
            <tr>
              <th>
                <div className="th-inner" style={{ minWidth: "auto" }}>
                  <span className="th-label">ID</span>
                </div>
              </th>
              <th>
                <div className="th-inner" style={{ minWidth: "auto" }}>
                  <span className="th-label">Name</span>
                </div>
              </th>
              <th>
                <div className="th-inner" style={{ minWidth: "auto" }}>
                  <span className="th-label">TT Account ID</span>
                </div>
              </th>
              <th>
                <div className="th-inner" style={{ minWidth: "auto" }}>
                  <span className="th-label">PNL Start</span>
                </div>
              </th>
              <th>
                <div className="th-inner" style={{ minWidth: "auto" }} />
              </th>
            </tr>
          </thead>
          <tbody>
            {accounts.map((a) => (
              <tr key={a.id}>
                <td>{a.id}</td>
                <td>{a.name}</td>
                <td>{a.tt_account_id}</td>
                <td>
                  {editingPnlStartId === a.id ? (
                    <span className="row" style={{ margin: 0 }}>
                      <DatePickerField
                        id={`pnl-start-edit-${a.id}`}
                        label=""
                        value={editingPnlStartValue}
                        onChange={setEditingPnlStartValue}
                      />
                      <button className="btn xs" onClick={() => savePnlStart(a)} disabled={pnlStartBusy}>
                        {pnlStartBusy && <Loader2 size={12} className="spin" />}
                        Save
                      </button>
                      <button className="btn secondary xs" onClick={() => setEditingPnlStartId(null)} disabled={pnlStartBusy}>
                        Cancel
                      </button>
                    </span>
                  ) : (
                    <button
                      type="button"
                      className="link-btn"
                      onClick={() => startEditPnlStart(a)}
                      title="Edit PNL start date"
                    >
                      {a.pnl_start_date || "Not set"}
                    </button>
                  )}
                </td>
                <td>
                  {pendingDeleteId === a.id ? (
                    <span className="row" style={{ margin: 0 }}>
                      <span className="status" style={{ margin: 0 }}>
                        Delete '{a.name}'?
                      </span>
                      <button className="btn danger xs" onClick={() => handleDelete(a.id)}>
                        Confirm
                      </button>
                      <button className="btn secondary xs" onClick={() => setPendingDeleteId(null)}>
                        Cancel
                      </button>
                    </span>
                  ) : (
                    <button
                      className="btn secondary xs icon-only"
                      onClick={() => setPendingDeleteId(a.id)}
                      aria-label={`Delete account ${a.name}`}
                      title="Delete account"
                    >
                      <Trash2 size={12} />
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {accounts.length === 0 && (
              <tr>
                <td colSpan={5} className="status">
                  No accounts yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
