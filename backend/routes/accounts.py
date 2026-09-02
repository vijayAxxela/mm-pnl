# routes/accounts.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional, Dict
from database.db import get_db, Account
from datetime import datetime
from typing import Any
from collections import defaultdict

router = APIRouter()

# Pydantic schemas
class AccountCreate(BaseModel):
    name: str  # User only provides the account name
    pnl_start_date: str  # YYYY-MM-DD (IST) — PNL tracking and fill backfill start from here

class PNLStartDateUpdate(BaseModel):
    pnl_start_date: str  # YYYY-MM-DD (IST)

class AccountResponse(BaseModel):
    id: int
    account_type: int
    company_id: int
    tt_account_id: int
    name: str
    parent_account_id: Optional[int]
    parent_id: Optional[int]
    revision: Optional[int]
    pnl_start_date: Optional[str] = None

    class Config:
        from_attributes = True

class FillResponse(BaseModel):
    account: str
    accountId: int
    instrumentId: str
    securityDesc: str
    side: int  # 1 = Buy, 2 = Sell
    orderId: str
    execId: str
    lastQty: float
    lastPx: float
    cumQty: float
    avgPx: float
    ordStatus: str
    ordType: int
    marketId: int
    transactTime: str
    tradeDate: str
    tradeMatchId: Optional[str] = None
    aggressorIndicator: str
    execType: int
    timeStamp: str
    
    class Config:
        from_attributes = True

class AccountFillsResponse(BaseModel):
    account_name: str
    account_id: int
    total_fills: int
    total_quantity: float
    buy_quantity: float
    sell_quantity: float
    fills: List[Dict[str, Any]]

class InstrumentSummary(BaseModel):
    instrument: str
    securityDesc: str
    total_fills: int
    buy_qty: float
    sell_qty: float
    net_qty: float
    avg_buy_price: float
    avg_sell_price: float
    total_buy_value: float
    total_sell_value: float

class AccountFillsSummaryResponse(BaseModel):
    account_name: str
    account_id: int
    total_fills: int
    total_instruments: int
    instruments: List[InstrumentSummary]



@router.post("/", response_model=AccountResponse)
def create_account(account: AccountCreate, db: Session = Depends(get_db)):
    """
    Create a new trading account by fetching details from TT API
    User only provides the account name, rest is fetched from TT
    """
    from main import tt_client  # Import shared tt_client from main
    from routes.TT_routes import IST
    from routes.fills import sync_fills_for_account

    # Check if account already exists in DB
    existing_account = db.query(Account).filter(
        Account.name == account.name
    ).first()

    if existing_account:
        raise HTTPException(status_code=400, detail="Account already exists in database")

    today_ist = datetime.now(IST).strftime('%Y-%m-%d')
    if account.pnl_start_date > today_ist:
        raise HTTPException(status_code=400, detail="PNL start date cannot be in the future")

    try:
        # Check if we have accounts loaded (from startup login)
        if not tt_client.accounts:
            # If not, login now
            login_result = tt_client.login(db_session=db)
            
            if login_result['status'] != 'success':
                raise HTTPException(
                    status_code=500, 
                    detail=f"Failed to connect to TT API: {login_result.get('message', 'Unknown error')}"
                )
        
        # Filter accounts by the provided name
        filtered_accounts = tt_client.filter_accounts([account.name])
        
        if not filtered_accounts:
            raise HTTPException(
                status_code=404, 
                detail=f"Account '{account.name}' not found in TT"
            )
        # print(filtered_accounts)
        if len(filtered_accounts) > 1:
            raise HTTPException(
                status_code=400,
                detail=f"Multiple accounts found matching '{account.name}'"
            )
        
        # Get the account data from TT
        tt_account = filtered_accounts[0]

        # Create account in our database with TT data
        db_account = Account(
            account_type=tt_account['accountType'],
            company_id=tt_account['companyId'],
            tt_account_id=tt_account['id'],
            name=tt_account['name'],
            parent_account_id=tt_account.get('parentAccountId'),
            parent_id=tt_account.get('parentId'),
            revision=tt_account.get('revision'),
            pnl_start_date=account.pnl_start_date
        )

        db.add(db_account)
        db.commit()
        db.refresh(db_account)

        # Backfill every fill from the chosen PNL start date through today,
        # so PNL has full data from day one. Best-effort: the account is
        # still successfully created even if this initial sync fails (e.g. a
        # transient TT hiccup) — the Fills page can always re-sync manually.
        try:
            sync_fills_for_account(db, tt_client, db_account, account.pnl_start_date, today_ist)
        except Exception as e:
            db.rollback()

        return db_account

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error creating account: {str(e)}")

@router.get("/", response_model=List[AccountResponse])
def get_accounts(db: Session = Depends(get_db)):
    """Get all accounts from database"""
    accounts = db.query(Account).all()
    return accounts

@router.get("/{account_id}", response_model=AccountResponse)
def get_account(account_id: int, db: Session = Depends(get_db)):
    """Get a specific account by database ID"""
    account = db.query(Account).filter(Account.id == account_id).first()
    
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    return account

@router.get("/by-name/{account_name}", response_model=AccountResponse)
def get_account_by_name(account_name: str, db: Session = Depends(get_db)):
    """Get a specific account by TT account name"""
    account = db.query(Account).filter(Account.name == account_name).first()
    
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    return account

@router.put("/{account_id}/pnl-start-date", response_model=AccountResponse)
def update_pnl_start_date(account_id: int, payload: PNLStartDateUpdate, db: Session = Depends(get_db)):
    """
    Change an account's PNL start date. /api/pnl/overview reads this field
    live on every request, so the change takes effect immediately — no
    recompute step needed.

    If the new date reaches further back than the previous one (or there
    was no baseline yet), automatically backfills fills for the
    newly-included range, the same way account creation does, so PNL isn't
    silently incomplete for that gap.
    """
    from main import tt_client
    from routes.TT_routes import IST
    from routes.fills import sync_fills_for_account

    account = db.query(Account).filter(Account.id == account_id).first()

    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    today_ist = datetime.now(IST).strftime('%Y-%m-%d')
    if payload.pnl_start_date > today_ist:
        raise HTTPException(status_code=400, detail="PNL start date cannot be in the future")

    old_date = account.pnl_start_date
    account.pnl_start_date = payload.pnl_start_date
    db.commit()
    db.refresh(account)

    if old_date is None or payload.pnl_start_date < old_date:
        try:
            sync_fills_for_account(db, tt_client, account, payload.pnl_start_date, today_ist)
        except Exception:
            db.rollback()

    return account


@router.delete("/{account_id}")
def delete_account(account_id: int, db: Session = Depends(get_db)):
    """Delete an account from database"""
    account = db.query(Account).filter(Account.id == account_id).first()
    
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    db.delete(account)
    db.commit()
    
    return {"message": f"Account '{account.name}' deleted successfully"}

@router.get("/fills/", response_model=List[AccountFillsSummaryResponse])
def get_fills_all_accounts(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Get fill summary for all accounts in database aggregated by instrument
    
    Query params:
    - start_date: Filter fills from this date (YYYY-MM-DD)
    - end_date: Filter fills until this date (YYYY-MM-DD)
    """
    from main import tt_client
    from routes.TT_routes import ist_date_to_ns
    from datetime import datetime

    # Get all accounts from database
    accounts = db.query(Account).all()

    if not accounts:
        raise HTTPException(
            status_code=404,
            detail="No accounts found in database"
        )

    results = []
    start_ns = ist_date_to_ns(start_date)
    end_ns = ist_date_to_ns(end_date, end_of_day=True)

    try:
        for account in accounts:
            # Fetch fills scoped to this account only (TT-side filter),
            # instead of pulling the whole company's fill history.
            account_fills = tt_client.get_all_fills(
                start_time_ns=start_ns,
                end_time_ns=end_ns,
                accountID=account.tt_account_id
            )
            # Filter by date again client-side, since TT doesn't reliably
            # honor minTimestamp/maxTimestamp server-side.
            filtered_fills = []
            if start_date or end_date:
                for fill in account_fills:
                    fill_timestamp = int(fill.get('transactTime', '0'))
                    fill_date = datetime.fromtimestamp(fill_timestamp / 1_000_000_000)
                    
                    if start_date:
                        start = datetime.fromisoformat(start_date + 'T00:00:00')
                        if fill_date < start:
                            continue
                    
                    if end_date:
                        end = datetime.fromisoformat(end_date + 'T23:59:59')
                        if fill_date > end:
                            continue
                    
                    filtered_fills.append(fill)
            else:
                filtered_fills = account_fills
            
            # Aggregate by instrument
            instrument_summary = defaultdict(lambda: {
                'instrument': '',
                'securityDesc': '',
                'total_fills': 0,
                'buy_qty': 0.0,
                'sell_qty': 0.0,
                'net_qty': 0.0,
                'avg_buy_price': 0.0,
                'avg_sell_price': 0.0,
                'total_buy_value': 0.0,
                'total_sell_value': 0.0
            })
            
            for fill in filtered_fills:
                instrument_id = fill.get('instrumentId')
                security_desc = fill.get('securityDesc', 'Unknown')
                side = fill.get('side')  # 1 = Buy, 2 = Sell
                qty = fill.get('lastQty', 0)
                price = fill.get('lastPx', 0)
                
                summary = instrument_summary[instrument_id]
                summary['instrument'] = instrument_id
                summary['securityDesc'] = security_desc
                summary['total_fills'] += 1
                
                if side == 1:  # Buy
                    summary['buy_qty'] += qty
                    summary['total_buy_value'] += qty * price
                elif side == 2:  # Sell
                    summary['sell_qty'] += qty
                    summary['total_sell_value'] += qty * price
                
                summary['net_qty'] = summary['buy_qty'] - summary['sell_qty']
            
            # Calculate average prices
            for summary in instrument_summary.values():
                if summary['buy_qty'] > 0:
                    summary['avg_buy_price'] = summary['total_buy_value'] / summary['buy_qty']
                if summary['sell_qty'] > 0:
                    summary['avg_sell_price'] = summary['total_sell_value'] / summary['sell_qty']
            
            # Add to results
            results.append({
                "account_name": account.name,
                "account_id": account.tt_account_id,
                "total_fills": len(filtered_fills),
                "total_instruments": len(instrument_summary),
                "instruments": list(instrument_summary.values())
            })
        
        return results
        
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Error fetching fill summary: {str(e)}"
        )
@router.post("/sync-from-tt")
def sync_accounts_from_tt(db: Session = Depends(get_db)):
    """
    Sync all accounts from TT API to database
    Useful for bulk import or refresh
    """
    from main import tt_client  # Import shared tt_client from main
    
    try:
        # Refresh accounts from TT
        login_result = tt_client.login(db_session=db)
        
        if login_result['status'] != 'success':
            raise HTTPException(
                status_code=500, 
                detail=f"Failed to connect to TT API: {login_result.get('message', 'Unknown error')}"
            )
        
        synced_count = 0
        skipped_count = 0
        
        for tt_account in tt_client.accounts:
            # Check if account already exists
            existing = db.query(Account).filter(
                Account.tt_account_id == tt_account['id']
            ).first()
            
            if existing:
                skipped_count += 1
                continue
            
            # Create new account
            db_account = Account(
                account_type=tt_account['accountType'],
                company_id=tt_account['companyId'],
                tt_account_id=tt_account['id'],
                name=tt_account['name'],
                parent_account_id=tt_account.get('parentAccountId'),
                parent_id=tt_account.get('parentId'),
                revision=tt_account.get('revision')
            )
            
            db.add(db_account)
            synced_count += 1
        
        db.commit()
        
        return {
            "message": "Accounts synced successfully",
            "synced": synced_count,
            "skipped": skipped_count,
            "total_in_tt": len(tt_client.accounts)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error syncing accounts: {str(e)}")