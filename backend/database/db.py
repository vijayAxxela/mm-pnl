# database/db.py
import json
import os
from sqlalchemy import create_engine, Column, Integer, String, Float, BigInteger, ForeignKey, UniqueConstraint , DateTime, Boolean, Text, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()


def _load_database_url() -> str:
    """
    Resolve the Postgres connection string, in priority order:
    1. DATABASE_URL env var (e.g. from a .env file) — the recommended way to
       point at Postgres without committing credentials to config.json.
    2. database.url in backend/config.json.

    No local/sqlite fallback — Postgres is required.
    """
    env_url = os.getenv("DATABASE_URL")
    if env_url:
        return env_url

    config_path = os.path.join(os.path.dirname(__file__), "..", "config.json")
    try:
        with open(config_path, "r") as f:
            config = json.load(f)
            config_url = config.get("database", {}).get("url")
            if config_url:
                return config_url
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    raise RuntimeError(
        "No database URL configured. Set DATABASE_URL in backend/.env, or "
        "database.url in backend/config.json, to a Postgres connection string."
    )


DATABASE_URL = _load_database_url()

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class Account(Base):
    __tablename__ = "accounts"
    
    id = Column(Integer, primary_key=True, index=True)
    account_type = Column(Integer, nullable=False)
    company_id = Column(Integer, nullable=False)
    tt_account_id = Column(BigInteger, unique=True, nullable=False, index=True)
    name = Column(String, unique=True, nullable=False, index=True)
    parent_account_id = Column(BigInteger, nullable=True)
    parent_id = Column(BigInteger, nullable=True)
    revision = Column(BigInteger, nullable=True)

    # Date (YYYY-MM-DD, IST) this account started being tracked for PNL —
    # set to the day it was added. PNL and fill backfill are scoped from here.
    pnl_start_date = Column(String, nullable=True)

    products = relationship("AccountProductSettings", back_populates="account", cascade="all, delete-orphan")
    fills = relationship("Fill", back_populates="account", cascade="all, delete-orphan")
    positions = relationship("Position", back_populates="account", cascade="all, delete-orphan")


class Product(Base):
    __tablename__ = "products"

    db_id = Column(Integer, primary_key=True, index=True)

    alias = Column(String)
    displayFactor = Column(Float)  # TT sends fractional values (e.g. 0.1), not always a whole number
    displayType = Column(Integer)
    expirationDate = Column(BigInteger)
    id = Column(String, index=True)               
    lastTradeDate = Column(BigInteger)
    marketId = Column(Integer)
    name = Column(String)
    pointValue = Column(Float)
    productFamilyId = Column(String)
    productId = Column(String, index=True)  # TT productId — joins to ProductFamily.id by value
    productSymbol = Column(String)
    productTypeId = Column(Integer)
    ricCode = Column(String)
    roundLotQty = Column(Integer)
    securityExchange = Column(Integer)
    securityId = Column(String)
    seriesTermId = Column(Integer)
    term = Column(String)
    tickSize = Column(Float)
    tickSizeDenominator = Column(Integer)
    tickSizeNumerator = Column(Integer)

    # TT's tickValue is sometimes wrong (confirmed on HKEX GDU: TT returns
    # the per-gram tick amount, 0.01, instead of the per-contract value,
    # pointValue x tickSize = 1000 x 0.01 = 10 — it never applied the
    # contract's unit-size multiplier). We store the CORRECTED value here
    # (pointValue x tickSize when that disagrees with TT's raw figure);
    # TT's original, unmodified value is still recoverable from `raw`.
    tickValue = Column(Float)
    tick_value_adjusted = Column(Boolean, default=False)

    # TT currency id the product trades in (from ttpds/product), used to
    # convert PNL to USD. Cached here so we only look it up once per product.
    currency_id = Column(String)

    # Full, unmodified TT instrument payload — guarantees nothing TT sends is
    # ever lost, even fields not (yet) mapped to a named column above (e.g.
    # comboTypeId/legs/marketDepth/metadata on combo instruments).
    raw = Column(JSON)


class ProductFamily(Base):
    """
    Product-family-level data from ttpds/product/{productId} — distinct from
    Product above, which is really contract/instrument-level data (one row
    per specific expiry/term). Cached here (write-through, keyed by TT's
    productId) so it's fetched from TT once and reused everywhere, with the
    full raw response kept so nothing TT returns is ever discarded. Joins to
    Product.productId by value (no DB-level FK: existing Product rows predate
    this table, and a strict FK would fail migrating against that data).
    """
    __tablename__ = "product_families"

    id = Column(String, primary_key=True)  # TT productId
    symbol = Column(String, index=True)
    name = Column(String)
    currency_id = Column(String)
    market_id = Column(Integer)
    product_type_id = Column(Integer)
    raw = Column(JSON)
    fetched_at = Column(DateTime, default=datetime.utcnow)


class Market(Base):
    __tablename__ = "markets"
    
    id = Column(Integer, primary_key=True, index=True)
    tt_market_id = Column(String, unique=True, nullable=False, index=True)  # TT's market ID
    name = Column(String, unique=True, nullable=False, index=True)

class AccountProductSettings(Base):
    __tablename__ = "account_product_settings"
    
    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    product_symbol = Column(String, nullable=False)  
    __table_args__ = (UniqueConstraint('account_id', 'product_symbol', name='unique_account_product'),)
    account = relationship("Account", back_populates="products")

class Fill(Base):
    __tablename__ = "fills"
    
    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)
    
    account_name = Column(String, nullable=False, index=True)
    tt_account_id = Column(BigInteger, nullable=False, index=True)
    aggressor_indicator = Column(String)
    algo_id = Column(BigInteger)
    alloc_id = Column(String)
    avg_px = Column(Float)
    broker_id = Column(Integer)
    client_ip = Column(String)
    cum_qty = Column(Float)
    curr_user_id = Column(BigInteger)
    delta_qty = Column(Float)
    exch_leaves_qty = Column(Float)
    exch_order_assoc = Column(String)
    exec_id = Column(String, unique=True, nullable=False, index=True)  # Unique identifier
    exec_inst = Column(Integer)
    exec_type = Column(Integer)
    externally_created = Column(String)
    handling_instr = Column(Integer)
    instrument_id = Column(String, nullable=False, index=True)
    last_px = Column(Float, nullable=False)
    last_qty = Column(Float, nullable=False)
    manual_fill = Column(Boolean)
    manual_order_indicator = Column(Boolean)
    market_id = Column(Integer, nullable=False, index=True)
    message_type = Column(Integer)
    multi_leg_reporting_type = Column(String)
    ord_status = Column(String)
    ord_type = Column(Integer)
    order_cross_prevention_type = Column(String)
    order_id = Column(String, nullable=False, index=True)
    parent_instrument_id = Column(String)
    parent_order_id = Column(String)
    position_effect = Column(String)
    record_id = Column(String, unique=True, index=True)
    report_id = Column(String)
    report_type = Column(String)
    secondary_cl_ord_id = Column(String)
    secondary_exec_id = Column(String)
    secondary_order_id = Column(String)
    security_desc = Column(String, index=True)  # Symbol
    sender_location_id = Column(String)
    sender_sub_id = Column(String)
    side = Column(Integer, nullable=False)  # 1=Buy, 2=Sell
    source = Column(Integer)
    synthetic_type = Column(Integer)
    text_a = Column(String)
    text_b = Column(String)
    text_c = Column(String)
    text_tt = Column(String)
    time_in_force = Column(Integer)
    time_sent_client = Column(String)
    time_sent_tt = Column(String)
    time_stamp = Column(String)
    trade_date = Column(String, index=True)
    trade_match_id = Column(String)
    trade_type = Column(String)
    trading_venue_trade_id = Column(String)
    transact_time = Column(String, nullable=False, index=True)  # Main timestamp
    transaction_type = Column(Integer)
    unique_exec_id = Column(String, unique=True, index=True)
    
    # Store complex fields as JSON
    fills_group = Column(JSON)
    parties = Column(JSON)
    report_sides = Column(JSON)

    # Full, unmodified TT fill payload — guarantees nothing TT sends is ever
    # lost, even fields not (yet) mapped to a named column above.
    raw = Column(JSON)

    # SHA-256 of the full raw payload — the "is this genuinely the same fill"
    # check used by the sync jobs, independent of exec_id (belt-and-braces:
    # a duplicate is skipped if either the exec_id or the whole row matches).
    row_hash = Column(String, unique=True, index=True, nullable=True)

    # Metadata
    created_at = Column(DateTime, default=datetime.now())
    
    # Relationships
    account = relationship("Account", back_populates="fills")

class Position(Base):
    __tablename__ = "positions"
    
    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)
    
    # Account info
    account_name = Column(String, nullable=False, index=True)
    tt_account_id = Column(BigInteger, nullable=False, index=True)
    
    # Instrument info
    instrument_id = Column(String, nullable=False, index=True)
    symbol = Column(String, nullable=False, index=True)
    product_symbol = Column(String, index=True)
    market_id = Column(Integer)
    tick_size = Column(Float, nullable=False)
    tick_value = Column(Float, nullable=False)
    currency = Column(String, default='USD')
    
    # Position data (FIFO)
    net_position = Column(Float, nullable=False, default=0)  # Positive=Long, Negative=Short
    net_open_price = Column(Float, nullable=False, default=0)  # Average price of open position
    buy_qty = Column(Float, nullable=False, default=0)
    sell_qty = Column(Float, nullable=False, default=0)
    buy_avg_price = Column(Float, nullable=False, default=0)
    sell_avg_price = Column(Float, nullable=False, default=0)
    
    # PNL (in currency, not ticks)
    realized_pnl = Column(Float, nullable=False, default=0)
    unrealized_pnl = Column(Float, nullable=False, default=0)
    total_pnl = Column(Float, nullable=False, default=0)
    
    sod_position = Column(Float, nullable=False, default=0)
    sod_price = Column(Float, nullable=False, default=0)
    
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Ensure one position per account-instrument pair
    __table_args__ = (UniqueConstraint('account_id', 'instrument_id', name='unique_account_instrument'),)
    
    account = relationship("Account", back_populates="positions")


class CurrencyRate(Base):
    """
    Daily-cached currency->USD conversion rate. Fetched from TT at most once
    per 'rate day' (a rate day runs 10:00 IST to the next day's 10:00 IST —
    see routes/TT_routes.get_daily_usd_rate), then reused for every PNL
    calculation during that window instead of re-querying TT each time.
    """
    __tablename__ = "currency_rates"

    id = Column(Integer, primary_key=True, index=True)
    currency_id = Column(String, nullable=False, index=True)
    rate_date = Column(String, nullable=False, index=True)  # YYYY-MM-DD anchor of the rate day
    rate = Column(Float, nullable=False)
    fetched_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint('currency_id', 'rate_date', name='unique_currency_rate_day'),)


class DailyPnlSnapshot(Base):
    """
    Snapshot of a contract's realized_pnl (unrealized isn't included as of
    now — see routes/pnl.snapshot_day_open_pnl) taken once per trading day,
    15 minutes after the trading day starts — 6:45 AM IST (see
    main._daily_snapshot_loop). Shown on the PNL page as "Day Open PNL", a
    baseline for movement since the day began. One row per (account,
    instrument, trading day); upserted, so re-running the snapshot for the
    same day is safe.
    """
    __tablename__ = "daily_pnl_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)
    instrument_id = Column(String, nullable=False, index=True)
    snapshot_date = Column(String, nullable=False, index=True)  # YYYY-MM-DD, IST trading day
    day_open_pnl = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('account_id', 'instrument_id', 'snapshot_date', name='unique_daily_snapshot'),
    )


class AlertSettings(Base):
    """
    Single-row (id=1) config for the combined-loss alert system (see
    routes/alerts.check_and_fire_loss_alerts, called after every scheduled
    fill sync in main.py). "Loss" = current combined realized PNL minus the
    combined Day Open PNL baseline, summed across every account — a sound
    alert (pushed over the websocket) fires each time that loss crosses a
    NEW higher multiple of sound_alert_step; an email fires each time it
    crosses a new higher multiple of email_alert_step. Both are step
    trackers, not simple threshold checks — see last_sound_threshold/
    last_email_threshold, which record the highest step already alerted on
    so a loss sitting between two steps (e.g. 700, between the 500 and
    1000 steps) never re-fires until it actually reaches the next one.
    Resets (both last_*_threshold back to 0) whenever `trading_day` no
    longer matches the current trading day, so each day starts fresh.
    """
    __tablename__ = "alert_settings"

    id = Column(Integer, primary_key=True)
    enabled = Column(Boolean, nullable=False, default=True)
    sound_alert_step = Column(Float, nullable=False, default=500.0)
    email_alert_step = Column(Float, nullable=False, default=1000.0)
    trading_day = Column(String, nullable=True)  # YYYY-MM-DD the thresholds below apply to
    last_sound_threshold = Column(Float, nullable=False, default=0.0)
    last_email_threshold = Column(Float, nullable=False, default=0.0)


class AlertEmail(Base):
    """One recipient for the loss-alert email (see AlertSettings)."""
    __tablename__ = "alert_emails"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, nullable=False, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class TTAccountCache(Base):
    """
    Write-through cache of every account TT returns for this company (not
    just the ones a user has added for PNL tracking via `Account` above) —
    fetched via TTClient.get_all_accounts() (now fully paginated) and synced
    on every login. Keeps the full raw payload so nothing TT sends is lost,
    even fields not mapped to a named column.
    """
    __tablename__ = "tt_account_cache"

    tt_account_id = Column(BigInteger, primary_key=True)
    account_type = Column(Integer, nullable=False)
    company_id = Column(Integer, nullable=False)
    name = Column(String, nullable=False, index=True)
    parent_account_id = Column(BigInteger, nullable=True)
    parent_id = Column(BigInteger, nullable=True)
    revision = Column(BigInteger, nullable=True)
    raw = Column(JSON)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class UiState(Base):
    """
    Small generic key -> JSON value store for UI state that should survive a
    reload (expanded tree nodes, active column filters, sort, etc). This app
    has no per-user accounts, so state is shared globally by a fixed key
    (e.g. 'pnl-tree') rather than being tied to a specific user.
    """
    __tablename__ = "ui_state"

    key = Column(String, primary_key=True)
    value = Column(JSON, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


def init_db():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()