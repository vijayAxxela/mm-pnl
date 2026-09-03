import { useEffect, useState } from "react";
import { api } from "../api.js";
import DatePickerField from "../components/DatePickerField.jsx";
import { Trash2, Loader2, Plus, Pencil } from "../components/icons.jsx";

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

// Strips everything but digits and drops leading zeros — the field only ever
// holds the magnitude; the "−" prefix shown next to it is fixed UI chrome,
// not part of the value, so the step can never be entered as non-negative.
function sanitizeStepDigits(raw) {
  return raw.replace(/[^0-9]/g, "").replace(/^0+(?=\d)/, "");
}

function AlertsSection() {
  const [enabled, setEnabled] = useState(true);
  const [enabledBusy, setEnabledBusy] = useState(false);
  const [soundStep, setSoundStep] = useState("500");
  const [emailStep, setEmailStep] = useState("1000");
  const [settingsBusy, setSettingsBusy] = useState(false);
  const [settingsStatus, setSettingsStatus] = useState(null);
  const [editingSettings, setEditingSettings] = useState(false);

  const [emails, setEmails] = useState([]);
  const [newEmail, setNewEmail] = useState("");
  const [emailBusy, setEmailBusy] = useState(false);
  const [pendingDeleteId, setPendingDeleteId] = useState(null);

  const [error, setError] = useState(null);

  const loadSettings = () => {
    api
      .getAlertSettings()
      .then((s) => {
        setEnabled(s.enabled);
        setSoundStep(String(s.sound_alert_step));
        setEmailStep(String(s.email_alert_step));
      })
      .catch((e) => setError(e.message));
  };

  const loadEmails = () => {
    api
      .listAlertEmails()
      .then(setEmails)
      .catch((e) => setError(e.message));
  };

  useEffect(() => {
    loadSettings();
    loadEmails();
  }, []);

  const handleSaveSettings = async (e) => {
    e.preventDefault();
    const sound = parseInt(soundStep, 10);
    const email = parseInt(emailStep, 10);
    if (!(sound >= 1) || !(email >= 1)) {
      setError("Both steps must be at least -1");
      return;
    }
    setError(null);
    setSettingsStatus(null);
    setSettingsBusy(true);
    try {
      await api.updateAlertSettings(enabled, sound, email);
      setSettingsStatus("Saved");
      setEditingSettings(false);
      setTimeout(() => setSettingsStatus(null), 3000);
    } catch (e) {
      setError(e.message);
    } finally {
      setSettingsBusy(false);
    }
  };

  const handleAddEmail = async (e) => {
    e.preventDefault();
    if (!newEmail.trim()) return;
    setError(null);
    setEmailBusy(true);
    try {
      await api.addAlertEmail(newEmail.trim());
      setNewEmail("");
      loadEmails();
    } catch (e) {
      setError(e.message);
    } finally {
      setEmailBusy(false);
    }
  };

  const handleDeleteEmail = async (id) => {
    setError(null);
    try {
      await api.deleteAlertEmail(id);
      setPendingDeleteId(null);
      loadEmails();
    } catch (e) {
      setError(e.message);
    }
  };

  const handleToggleEnabled = async () => {
    const next = !enabled;
    setEnabled(next);
    setError(null);
    setEnabledBusy(true);
    try {
      await api.updateAlertEnabled(next);
    } catch (e) {
      setEnabled(!next);
      setError(e.message);
    } finally {
      setEnabledBusy(false);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", minWidth: 0, flex: "0 1 380px" }}>
      <div className="page-header">
        <h2>Loss Alerts</h2>
        <label className="switch" title={enabled ? "Alerts on" : "Alerts off"}>
          <span className="switch-label">{enabled ? "On" : "Off"}</span>
          <input
            type="checkbox"
            checked={enabled}
            disabled={enabledBusy}
            onChange={handleToggleEnabled}
            aria-label="Loss alerts enabled"
          />
          <span className="switch-track" />
        </label>
      </div>

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      <div className="panel" style={{ marginBottom: "var(--space-3)" }}>
        <form className="row" onSubmit={handleSaveSettings} style={{ flexWrap: "nowrap", marginBottom: "var(--space-3)" }}>
          <div className="field">
            <label htmlFor="sound-step">Sound alert every</label>
            <span className="neg-input">
              <span className="neg-input-sign" aria-hidden="true">−</span>
              <input
                id="sound-step"
                type="text"
                inputMode="numeric"
                value={soundStep}
                onChange={(e) => setSoundStep(sanitizeStepDigits(e.target.value))}
                readOnly={!editingSettings}
                style={{ width: `${Math.max(6, soundStep.length + 1)}ch` }}
              />
            </span>
          </div>
          <div className="field">
            <label htmlFor="email-step">Email alert every</label>
            <span className="neg-input">
              <span className="neg-input-sign" aria-hidden="true">−</span>
              <input
                id="email-step"
                type="text"
                inputMode="numeric"
                value={emailStep}
                onChange={(e) => setEmailStep(sanitizeStepDigits(e.target.value))}
                readOnly={!editingSettings}
                style={{ width: `${Math.max(6, emailStep.length + 1)}ch` }}
              />
            </span>
          </div>
          {editingSettings ? (
            <button className="btn" type="submit" disabled={settingsBusy} style={{ flexShrink: 0 }}>
              {settingsBusy && <Loader2 size={13} className="spin" />}
              {settingsBusy ? "Saving..." : "Save"}
            </button>
          ) : (
            <button
              type="button"
              className="btn secondary xs"
              style={{ flexShrink: 0, alignSelf: "flex-end" }}
              onClick={(e) => {
                e.preventDefault();
                setSettingsStatus(null);
                setEditingSettings(true);
              }}
            >
              <Pencil size={12} />
              Edit
            </button>
          )}
          {!editingSettings && settingsStatus && (
            <span className="status" style={{ margin: 0 }}>
              {settingsStatus}
            </span>
          )}
        </form>

        <form className="row" onSubmit={handleAddEmail} style={{ flexWrap: "wrap", margin: 0 }}>
          <div className="field">
            <label htmlFor="new-alert-email">Add email for loss alerts</label>
            <input
              id="new-alert-email"
              type="email"
              placeholder="name@example.com"
              value={newEmail}
              onChange={(e) => setNewEmail(e.target.value)}
              style={{ width: "auto", minWidth: "24ch" }}
            />
          </div>
          <button className="btn" type="submit" disabled={emailBusy}>
            {emailBusy ? <Loader2 size={13} className="spin" /> : <Plus size={13} />}
            {emailBusy ? "Adding..." : "Add"}
          </button>
        </form>
      </div>

      <div className="table-wrap" style={{ maxHeight: "none", width: "100%" }}>
        <table className="table-loose" style={{ width: "100%" }}>
          <thead>
            <tr>
              <th>
                <div className="th-inner" style={{ minWidth: "auto" }}>
                  <span className="th-label">Email</span>
                </div>
              </th>
              <th>
                <div className="th-inner" style={{ minWidth: "auto" }} />
              </th>
            </tr>
          </thead>
          <tbody>
            {emails.map((e) => (
              <tr key={e.id}>
                <td>{e.email}</td>
                <td>
                  {pendingDeleteId === e.id ? (
                    <span className="row" style={{ margin: 0 }}>
                      <span className="status" style={{ margin: 0 }}>
                        Remove '{e.email}'?
                      </span>
                      <button className="btn danger xs" onClick={() => handleDeleteEmail(e.id)}>
                        Confirm
                      </button>
                      <button className="btn secondary xs" onClick={() => setPendingDeleteId(null)}>
                        Cancel
                      </button>
                    </span>
                  ) : (
                    <button
                      className="btn secondary xs icon-only"
                      onClick={() => setPendingDeleteId(e.id)}
                      aria-label={`Remove ${e.email}`}
                      title="Remove email"
                    >
                      <Trash2 size={12} />
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {emails.length === 0 && (
              <tr>
                <td colSpan={2} className="status">
                  No alert emails added yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
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
    <div className="row" style={{ alignItems: "flex-start", flexWrap: "wrap", gap: "var(--space-6)" }}>
    <div style={{ display: "flex", flexDirection: "column", minWidth: 0, flex: "0 0 auto" }}>
      <div className="page-header">
        <h2>Accounts</h2>
        <span className="status">{accounts.length} account{accounts.length === 1 ? "" : "s"}</span>
      </div>

      <div className="panel" style={{ width: "fit-content", maxWidth: "100%" }}>
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

      <div style={{ alignSelf: "stretch", width: 1, background: "var(--color-border)" }} aria-hidden="true" />

      <AlertsSection />
    </div>
  );
}
