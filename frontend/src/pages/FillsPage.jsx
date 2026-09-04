import { useEffect, useState } from "react";
import { api } from "../api.js";
import DataTable, { ExportMenu } from "../components/DataTable.jsx";
import DatePickerField from "../components/DatePickerField.jsx";
import { useClickOutside } from "../utils/useClickOutside.js";
import { Search, Loader2, ArrowRight } from "../components/icons.jsx";

// Fixed set, in this order — see backend routes/fills.py's
// format_fills_for_frontend, which already excludes per-leg spread rows
// (multiLegReportingType == '2') and resolves exchange/contract/algo/user
// names server-side. Nothing here is auto-derived from raw TT fields.
const FILLS_COLUMNS = [
  { key: "time", label: "Time" },
  { key: "exchange", label: "Exchange" },
  { key: "contract", label: "Contract" },
  { key: "side", label: "B/S" },
  { key: "price", label: "Price", align: "right" },
  { key: "fill_qty", label: "FillQty", align: "right" },
  { key: "account", label: "Account" },
  { key: "manual_fill", label: "ManualFill", render: (f) => (f.manual_fill ? "Yes" : "No") },
  { key: "algo_id", label: "AlgoId" },
  { key: "curr_user_id", label: "CurrentUserId", render: (f) => f.curr_user_name || f.curr_user_id || "" },
  { key: "order_id", label: "OrderId" },
  { key: "parent_id", label: "ParentID" },
];

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

function AccountPicker({ accounts, selectedAccount, onSelect, onClear }) {
  // The input box itself never gets swapped out for plain text — once an
  // account is picked its name just becomes the input's value. Editing that
  // text invalidates the pick (until a new one is chosen from the dropdown).
  const [search, setSearch] = useState(selectedAccount?.name || "");
  const [open, setOpen] = useState(false);
  const ref = useClickOutside(() => setOpen(false));

  const matches = open
    ? (search.trim()
        ? accounts.filter((a) => a.name.toLowerCase().includes(search.trim().toLowerCase()))
        : accounts
      ).slice(0, 5)
    : [];

  // Fixed width, matching the suggestion dropdown's width exactly, so
  // the suggested rows never look wider than the box that opened them.
  const ACCOUNT_BOX_WIDTH = 150;

  const handleChange = (e) => {
    setSearch(e.target.value);
    setOpen(true);
    if (selectedAccount) onClear();
  };

  const handleSelect = (a) => {
    setSearch(a.name);
    setOpen(false);
    onSelect(a);
  };

  return (
    <div className="field" ref={ref} style={{ position: "relative", width: ACCOUNT_BOX_WIDTH }}>
      <label htmlFor="fills-account-search">Account</label>
      <div className="search-field">
        <Search size={13} className="search-field-icon" aria-hidden="true" />
        <input
          id="fills-account-search"
          className="search-field-input"
          placeholder="Search..."
          value={search}
          onChange={handleChange}
          onFocus={() => setOpen(true)}
          style={{ width: "100%", height: "var(--control-h)", padding: "0 var(--space-3) 0 28px" }}
          autoComplete="off"
        />
      </div>
      {open && (
        <div
          role="listbox"
          aria-label="Available accounts"
          className="autocomplete-panel"
          style={{ width: ACCOUNT_BOX_WIDTH }}
        >
          {matches.map((a) => (
            <button type="button" role="option" key={a.id} onClick={() => handleSelect(a)} className="account-option">
              {a.name}
            </button>
          ))}
          {matches.length === 0 && (
            <div className="col-filter-empty">{search.trim() ? "No matching accounts" : "No accounts available"}</div>
          )}
        </div>
      )}
    </div>
  );
}

export default function FillsPage() {
  const [accounts, setAccounts] = useState([]);
  const [selectedAccount, setSelectedAccount] = useState(null);
  const [startDate, setStartDate] = useState(todayIST());
  const [endDate, setEndDate] = useState(todayIST());
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .listAccounts()
      .then(setAccounts)
      .catch((e) => setError(e.message));
  }, []);

  const handleFetch = async () => {
    if (!selectedAccount || !startDate || !endDate) return;
    if (endDate < startDate) {
      setError("End date must be on or after start date");
      return;
    }
    setError(null);
    setBusy(true);
    try {
      setResult(await api.syncFillsForAccountDateRange(selectedAccount.name, startDate, endDate));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page-fill page-fill--fit">
      <div className="panel">
        <div className="row">
          <AccountPicker
            accounts={accounts}
            selectedAccount={selectedAccount}
            onSelect={(a) => {
              setSelectedAccount(a);
              setResult(null);
            }}
            onClear={() => {
              setSelectedAccount(null);
              setResult(null);
            }}
          />
          <DatePickerField id="fills-start-date" label="From" value={startDate} onChange={setStartDate} />
          <DatePickerField id="fills-end-date" label="To" value={endDate} onChange={setEndDate} />
          <button
            className="btn icon-only"
            onClick={handleFetch}
            disabled={busy || !selectedAccount}
            aria-label="Fetch & save"
            title="Fetch & save"
          >
            {busy ? <Loader2 size={15} className="spin" /> : <ArrowRight size={15} />}
          </button>
          {error && (
            <span className="error" style={{ margin: 0 }}>
              {error}
            </span>
          )}
          {result && (
            <ExportMenu
              rows={result.formatted_fills}
              columns={FILLS_COLUMNS}
              filename={`fills_${selectedAccount.name}_${result.start_date}_to_${result.end_date}`}
              style={{ marginLeft: "auto" }}
            />
          )}
        </div>
      </div>

      {result && <DataTable rows={result.formatted_fills} columns={FILLS_COLUMNS} keyField="exec_id" />}
    </div>
  );
}
