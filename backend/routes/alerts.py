# routes/alerts.py
"""
Combined-loss alerting. "Loss" = today's combined realized PNL (summed
across every account) minus the combined Day Open PNL baseline for today —
i.e. how much has been given back since the trading day opened. A sound
alert (pushed to every connected browser over the websocket) fires each
time that loss crosses a NEW higher multiple of `sound_alert_step`; an
email fires each time it crosses a new higher multiple of
`email_alert_step`. Both thresholds and the recipient list are user-
configurable (see the /settings and /emails endpoints below, driving the
frontend's Alerts page).

Realized-only, matching Day Open PNL's own basis (see
pnl.snapshot_day_open_pnl) — unrealized PNL depends on a manually-typed
"current price" that isn't reliable enough to build an alert threshold on,
same reasoning as why the Day Open snapshot itself excludes it.

check_and_fire_loss_alerts is called from main.py's fill-sync loop, right
after each scheduled sync — but only actually alerts once a Day Open
snapshot exists for the current trading day (before that, there's no
baseline to measure a "loss" against).
"""
import logging
import os
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

# Deliberately not pydantic's EmailStr — that needs the optional
# email-validator package, which isn't in uv.lock, so relying on it would
# only work in dev environments where it happens to already be installed
# ambiently and break the Docker build. A plain regex is good enough here.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

from database.db import get_db, AlertSettings, AlertEmail, DailyPnlSnapshot

router = APIRouter()
logger = logging.getLogger(__name__)


def _get_or_create_settings(db: Session) -> AlertSettings:
    settings = db.query(AlertSettings).filter(AlertSettings.id == 1).first()
    if not settings:
        settings = AlertSettings(id=1)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


class AlertSettingsResponse(BaseModel):
    enabled: bool
    sound_alert_step: float
    email_alert_step: float

    class Config:
        from_attributes = True


class AlertSettingsUpdate(BaseModel):
    enabled: bool
    sound_alert_step: float
    email_alert_step: float


class AlertEnabledUpdate(BaseModel):
    enabled: bool


class AlertEmailResponse(BaseModel):
    id: int
    email: str

    class Config:
        from_attributes = True


class AlertEmailCreate(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip()
        if not _EMAIL_RE.match(v):
            raise ValueError("Invalid email address")
        return v


@router.get("/settings", response_model=AlertSettingsResponse)
def get_alert_settings(db: Session = Depends(get_db)):
    return _get_or_create_settings(db)


@router.put("/settings", response_model=AlertSettingsResponse)
def update_alert_settings(payload: AlertSettingsUpdate, db: Session = Depends(get_db)):
    if payload.sound_alert_step <= 0 or payload.email_alert_step <= 0:
        raise HTTPException(status_code=400, detail="Alert steps must be positive")

    settings = _get_or_create_settings(db)
    settings.enabled = payload.enabled
    settings.sound_alert_step = payload.sound_alert_step
    settings.email_alert_step = payload.email_alert_step
    db.commit()
    db.refresh(settings)
    return settings


@router.put("/settings/enabled", response_model=AlertSettingsResponse)
def update_alert_enabled(payload: AlertEnabledUpdate, db: Session = Depends(get_db)):
    settings = _get_or_create_settings(db)
    settings.enabled = payload.enabled
    db.commit()
    db.refresh(settings)
    return settings


@router.get("/emails", response_model=List[AlertEmailResponse])
def list_alert_emails(db: Session = Depends(get_db)):
    return db.query(AlertEmail).order_by(AlertEmail.email).all()


@router.post("/emails", response_model=AlertEmailResponse)
def add_alert_email(payload: AlertEmailCreate, db: Session = Depends(get_db)):
    existing = db.query(AlertEmail).filter(AlertEmail.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already added")

    entry = AlertEmail(email=payload.email)
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


@router.delete("/emails/{email_id}")
def delete_alert_email(email_id: int, db: Session = Depends(get_db)):
    entry = db.query(AlertEmail).filter(AlertEmail.id == email_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Email not found")
    db.delete(entry)
    db.commit()
    return {"status": "ok"}


def _send_loss_email(
    recipients: List[str],
    account_names: List[str],
    loss: float,
    current_total: float,
    day_open_total: float,
    trading_day: str,
):
    """
    Best-effort — a missing/misconfigured SMTP setup logs a warning and is
    swallowed rather than breaking the fill-sync loop (an alert email
    failing to send shouldn't stop fills from syncing).
    """
    host = os.getenv("SMTP_HOST")
    if not host or not recipients:
        if not host:
            logger.warning("Loss alert email skipped: SMTP_HOST not configured")
        return

    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    from_addr = os.getenv("SMTP_FROM", user or "alerts@mm-pnl")

    from routes.TT_routes import IST, TRADING_DAY_START_HOUR, TRADING_DAY_START_MINUTE
    from datetime import datetime

    names = ", ".join(account_names) if account_names else "Accounts"

    now = datetime.now(IST)
    current_date = now.strftime("%Y-%m-%d")
    current_time = now.strftime("%H:%M")
    day_open_time = f"{TRADING_DAY_START_HOUR:02d}:{TRADING_DAY_START_MINUTE:02d}"

    def highlight(value: float) -> str:
        # Highlighted like a spreadsheet cell — colored background carries
        # the sign (red = loss, green = gain) so the number reads at a
        # glance instead of needing to parse the sign character.
        bg = "#ffd2d6" if value < 0 else "#d2f5dc" if value > 0 else "#eeeeee"
        fg = "#8a1c26" if value < 0 else "#166534" if value > 0 else "#555555"
        return (
            f'<b style="background:{bg};color:{fg};padding:2px 8px;'
            f'border-radius:4px;font-family:monospace;">{value:.2f}</b>'
        )

    text_body = (
        f"Today's Loss : {loss:.2f}\n"
        # f"Current ({current_date} {current_time}) P&L: {current_total:.2f}\n"
        # f"Day Open ({trading_day} {day_open_time}) P&L: {day_open_total:.2f}\n"
    )
    html_body = (
        '<div style="font-family:Arial,sans-serif;font-size:14px;color:#111;">'
        f"<p>Today's Loss ({current_date} {current_time}) P&amp;L: {highlight(loss)}</p>"
        # f"<p>Day Open ({trading_day} {day_open_time}) P&amp;L: {highlight(day_open_total)}</p>"
        "</div>"
    )

    msg = MIMEMultipart("alternative")
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))
    msg["Subject"] = f"Live Loss Alert : -{loss:.2f}"
    msg["From"] = from_addr
    msg["To"] = ", ".join(recipients)

    try:
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.starttls()
            if user and password:
                server.login(user, password)
            server.sendmail(from_addr, recipients, msg.as_string())
        logger.info(f"Loss alert email sent to {len(recipients)} recipient(s)")
    except Exception as e:
        logger.error(f"Failed to send loss alert email: {e}")


def check_and_fire_loss_alerts(db: Session, tt_client) -> Optional[dict]:
    """
    Called from main.py's fill-sync loop after each scheduled sync — this
    function itself is fully synchronous (runs in a worker thread, like the
    fill sync itself) and does NOT push the websocket message directly;
    it returns a dict with a "sound_alert" key when one should be sent, and
    the caller (back on the async event loop) does the actual
    `await ws.broadcast(...)`. This keeps this module free of asyncio
    entirely, while email sending (also side-effecting, but synchronous)
    happens directly here via smtplib.

    Returns a dict describing what fired (for logging/broadcasting), or
    None if nothing happened (no Day Open snapshot yet today, or no new
    threshold crossed).

    Mirrors the PNL page's own TOTAL row exactly (same rows, same "Change
    from Open" basis — see PnlTree.jsx/routes/pnl.py's get_pnl_overview),
    so the combined figure in an alert email always matches what's on
    screen:
      - Contracts with no activity today AND no open position (buy=sell=
        net=0) are excluded from the sum, same as the frontend's own
        filteredRows — a flat, untouched-today position's old PNL
        shouldn't count toward an intraday loss alert.
      - "Current" includes unrealized PNL too, computed the same way the
        frontend does (client-typed current prices, read here from the
        same ui_state row the frontend saves them to) — not realized-only.
    A contract with no snapshot yet today defaults to 0.0, same as
    get_pnl_overview — if that ever produces a misleading number, backfill
    it manually with scripts/backfill_day_open_snapshot.py rather than
    computing a live fallback here.
    """
    from routes.pnl import _compute_pnl_rows
    from routes.fills import _trading_day_window_ns
    from database.db import UiState

    # The trading day's own calendar date, NOT plain today's-date-at-midnight
    # — the trading day rolls over at TRADING_DAY_START_TIME (e.g. 6:00 AM
    # IST), same boundary snapshot_day_open_pnl/_compute_pnl_rows use, so
    # between midnight and that time this still needs to check against
    # YESTERDAY's snapshot/thresholds (still the current trading day)
    # instead of going dormant early looking for today's, which hasn't been
    # taken yet.
    _, _, window_start, _ = _trading_day_window_ns()
    today = window_start.strftime('%Y-%m-%d')

    # No baseline yet for the current trading day — nothing to measure a
    # "loss since day open" against, so skip entirely rather than alerting
    # off a stale/zeroed previous day's snapshot.
    has_snapshot_today = db.query(DailyPnlSnapshot).filter(DailyPnlSnapshot.snapshot_date == today).first()
    if not has_snapshot_today:
        return None

    settings = _get_or_create_settings(db)
    if not settings.enabled:
        return None

    rows = _compute_pnl_rows(db, tt_client)
    if not rows:
        return None

    # Same zero-activity-today exclusion as PnlTree.jsx's filteredRows.
    rows = [r for r in rows if not (r["buy_qty"] == 0 and r["sell_qty"] == 0 and r["open_qty"] == 0)]
    if not rows:
        return None

    snapshots = db.query(DailyPnlSnapshot).filter(DailyPnlSnapshot.snapshot_date == today).all()
    day_open_by_key = {(s.account_id, s.instrument_id): s.day_open_pnl for s in snapshots}

    # Same manually-typed current prices the frontend reads/saves under this
    # exact ui_state key (see PRICES_KEY in PnlTree.jsx) — global, not
    # per-user, so this is literally the same numbers shown on screen.
    prices_state = db.query(UiState).filter(UiState.key == "pnl-current-prices").first()
    current_prices = prices_state.value or {} if prices_state else {}

    def unrealized_of(row: dict) -> float:
        price = current_prices.get(str(row["instrument_id"]))
        if price is None or not row["open_qty"] or not row["tick_size"]:
            return 0.0
        direction = 1 if row["open_qty"] > 0 else -1
        price_diff = direction * (price - row["avg_open_price"])
        unrealized_native = (price_diff / row["tick_size"]) * row["tick_value"] * abs(row["open_qty"])
        return unrealized_native * row.get("usd_rate", 1.0)

    current_total = sum(r["realized_pnl"] + unrealized_of(r) for r in rows)
    day_open_total = sum(day_open_by_key.get((r["account_id"], r["instrument_id"]), 0.0) for r in rows)

    diff = current_total - day_open_total
    loss = -diff if diff < 0 else 0.0

    # A new trading day starts this tracking over — yesterday's alerted
    # thresholds have no bearing on today's loss.
    if settings.trading_day != today:
        settings.trading_day = today
        settings.last_sound_threshold = 0.0
        settings.last_email_threshold = 0.0

    result = {"loss": round(loss, 2), "current_total": round(current_total, 2), "day_open_total": round(day_open_total, 2)}

    # floor(loss / step) * step — the highest step-multiple the current
    # loss has actually reached. Only fires when that's a NEW high (never
    # on the way back down, and never re-fires for a loss sitting between
    # two already-passed steps, e.g. 700 between the 500 and 1000 steps).
    if loss > 0 and settings.sound_alert_step > 0:
        sound_level = (loss // settings.sound_alert_step) * settings.sound_alert_step
        if sound_level > settings.last_sound_threshold:
            settings.last_sound_threshold = sound_level
            result["sound_alert"] = {"type": "loss_alert", "loss": round(loss, 2), "threshold": sound_level}
            logger.warning(f"Loss sound alert: combined loss {loss:.2f} crossed {sound_level}")

    if loss > 0 and settings.email_alert_step > 0:
        email_level = (loss // settings.email_alert_step) * settings.email_alert_step
        if email_level > settings.last_email_threshold:
            settings.last_email_threshold = email_level
            recipients = [e.email for e in db.query(AlertEmail).all()]
            account_names = list(dict.fromkeys(r["account_name"] for r in rows))
            _send_loss_email(recipients, account_names, loss, current_total, day_open_total, today)
            result["email_alert_threshold"] = email_level
            logger.warning(f"Loss email alert: combined loss {loss:.2f} crossed {email_level}")

    db.commit()
    return result if len(result) > 3 else None
