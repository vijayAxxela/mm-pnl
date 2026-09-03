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
    recipients: List[str], account_names: List[str], loss: float, current_total: float, day_open_total: float
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

    current_time = datetime.now(IST).strftime("%H:%M")
    day_open_time = f"{TRADING_DAY_START_HOUR:02d}:{TRADING_DAY_START_MINUTE:02d}"

    body = (
        f"Current ({current_time}) P&L: {current_total:.2f}\n"
        f"Day Open ({day_open_time}) P&L: {day_open_total:.2f}\n"
    )
    msg = MIMEText(body)
    msg["Subject"] = f"{names} Loss Alert : -{loss:.2f}"
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
    """
    from routes.pnl import _compute_pnl_rows
    from routes.TT_routes import IST
    from datetime import datetime

    today = datetime.now(IST).strftime('%Y-%m-%d')

    # No baseline yet today — nothing to measure a "loss since day open"
    # against, so skip entirely rather than alerting off a stale/zeroed
    # previous day's snapshot.
    has_snapshot_today = db.query(DailyPnlSnapshot).filter(DailyPnlSnapshot.snapshot_date == today).first()
    if not has_snapshot_today:
        return None

    settings = _get_or_create_settings(db)
    if not settings.enabled:
        return None

    rows = _compute_pnl_rows(db, tt_client)
    if not rows:
        return None

    snapshots = db.query(DailyPnlSnapshot).filter(DailyPnlSnapshot.snapshot_date == today).all()
    day_open_by_key = {(s.account_id, s.instrument_id): s.day_open_pnl for s in snapshots}

    current_total = sum(r["realized_pnl"] for r in rows)
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
            _send_loss_email(recipients, account_names, loss, current_total, day_open_total)
            result["email_alert_threshold"] = email_level
            logger.warning(f"Loss email alert: combined loss {loss:.2f} crossed {email_level}")

    db.commit()
    return result if len(result) > 3 else None
