# TT_routes.py
import time
import requests
import logging
from uuid import uuid4
from simplejson import JSONDecodeError
from sqlalchemy.orm import Session
import hmac
import hashlib
import base64
from datetime import datetime, timedelta
from typing import Optional, Dict, List
import os
import pandas as pd
import pytz

IST = pytz.timezone('Asia/Kolkata')


def _load_trading_day_start() -> tuple[int, int]:
    """
    Reads TRADING_DAY_START_TIME (HH:MM, IST) from the environment, e.g.
    'TRADING_DAY_START_TIME=06:00' in backend/.env, so this can be changed
    without a code edit. Falls back to 06:00 if unset or malformed.
    """
    raw = os.getenv("TRADING_DAY_START_TIME", "06:00")
    try:
        hour_str, minute_str = raw.split(":")
        hour, minute = int(hour_str), int(minute_str)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
        return hour, minute
    except (ValueError, AttributeError):
        logging.getLogger(__name__).warning(
            f"Invalid TRADING_DAY_START_TIME={raw!r}, expected HH:MM — falling back to 06:00"
        )
        return 6, 0


# The trading-day boundary used consistently everywhere a calendar date gets
# turned into a fill-query window — PNL start-date filtering, account
# backfill, manual Fills-page date range, and the scheduler's fill sync (see
# fills._trading_day_window_ns, which reuses these same constants) all treat
# a "day" as running from this time IST to the same time IST the next day,
# not midnight to midnight, so PNL and fill fetching always agree on what a
# given date actually covers. Configurable via TRADING_DAY_START_TIME.
TRADING_DAY_START_HOUR, TRADING_DAY_START_MINUTE = _load_trading_day_start()


def ist_date_to_ns(date_str: Optional[str], end_of_day: bool = False) -> Optional[int]:
    """
    Convert a 'YYYY-MM-DD' calendar date to nanoseconds since epoch, anchored
    to the trading-day boundary (see TRADING_DAY_START_HOUR/MINUTE) rather
    than midnight. end_of_day gives the last moment of that trading day —
    one microsecond before the start time on the following calendar date,
    i.e. just before the next trading day begins.
    """
    if not date_str:
        return None

    base_date = datetime.strptime(date_str, '%Y-%m-%d')
    start_of_trading_day = base_date.replace(hour=TRADING_DAY_START_HOUR, minute=TRADING_DAY_START_MINUTE)

    dt = start_of_trading_day + timedelta(days=1) - timedelta(microseconds=1) if end_of_day else start_of_trading_day
    dt = IST.localize(dt)

    return int(dt.timestamp() * 1_000_000_000)


def _current_rate_date() -> str:
    """
    The 'rate day' anchor date: rolls over at 10:00 IST rather than midnight,
    so a currency rate fetched today at/after 10am IST stays in effect until
    10am IST tomorrow.
    """
    now_ist = datetime.now(IST)
    anchor = now_ist if now_ist.hour >= 10 else now_ist - timedelta(days=1)
    return anchor.strftime('%Y-%m-%d')


def get_daily_usd_rate(db: Session, tt_client: "TTClient", currency_id) -> float:
    """
    currency_id -> USD conversion rate for the current rate day, fetched from
    TT and cached in currency_rates at most once per rate day (see
    _current_rate_date) — every PNL calculation during that window reuses the
    same cached rate instead of re-querying TT.
    """
    from database.db import CurrencyRate

    if currency_id is None:
        return 1.0

    currency_id = str(currency_id)
    if currency_id == "151":  # TT's USD currency id — no conversion needed
        return 1.0

    rate_date = _current_rate_date()

    cached = db.query(CurrencyRate).filter(
        CurrencyRate.currency_id == currency_id,
        CurrencyRate.rate_date == rate_date
    ).first()
    if cached:
        return cached.rate

    rate = tt_client.get_usd_rate(currency_id)
    if rate is None:
        rate = 1.0  # don't let a missing FX rate blow up the whole PNL calc

    db.add(CurrencyRate(currency_id=currency_id, rate_date=rate_date, rate=rate))
    db.commit()

    return rate


class TTClient:
    """Trading Technologies API Client"""
    
    def __init__(self, api_key: str, api_secret: str, environment: str = 'ext_prod_live'):
        """
        Initialize TT API Client
        
        Args:
            api_key: TT API Key
            api_secret: TT API Secret
            environment: TT environment ('ext_uat_cert' or 'ext_prod_live')
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.environment = environment
        self.base_url = 'https://ttrestapi.trade.tt'
        self.bearer_token = None
        self.token_expiry = None
        self.accounts = []
        
        # Setup logging
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)
    
    def generate_hmac_header(self, http_method: str, url_path: str) -> Dict[str, str]:
        """
        Generate HMAC authentication header
        
        Args:
            http_method: HTTP method (GET, POST, etc.)
            url_path: URL path without base URL
            
        Returns:
            Dictionary with authentication headers
        """
        timestamp = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
        message = f"{http_method.upper()} {url_path} {timestamp}"
        
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).digest()
        
        signature_b64 = base64.b64encode(signature).decode('utf-8')
        auth_header = f'HMAC key="{self.api_key}", signature="{signature_b64}", timestamp="{timestamp}"'
        
        return {
            'Authorization': auth_header,
            'Content-Type': 'application/json',
            'x-api-key': self.api_key
        }
    
    def api_request(
        self,
        url: str,
        headers: Dict[str, str],
        data: Optional[Dict] = None,
        http_method: str = 'get',
        request_timeout: bool = False,
        user_params: Dict = {},
        requires_auth: bool = True,
    ) -> Dict:
        """
        Make API request to TT.

        Args:
            url: Full URL for the request
            headers: Request headers
            data: Request body data
            http_method: HTTP method
            request_timeout: Whether to raise error on timeout
            user_params: Additional query parameters
            requires_auth: When True (the default), logs in (retrieves a
                fresh bearer token) immediately before this request and
                injects it into `headers` under 'Authorization' — every real
                TT endpoint call goes through this, so a request is never
                made against a token that might have gone stale, and every
                call site's own bearer_token is guaranteed current. False
                only for the token endpoint itself (retrieve_token calling
                this would otherwise recurse) and HMAC-authenticated calls
                like create_user that don't use a bearer token at all.

        Returns:
            Response JSON data
        """
        if requires_auth:
            self.retrieve_token()
            headers = {**headers, 'Authorization': self.bearer_token}

        company_name = os.getenv('COMPANY_NAME', 'TRADING_COMPANY')
        req_id = f'ttapi-{company_name}--{uuid4()}'
        self.logger.info(f"Request ID: {req_id}")
        
        params = {**user_params, 'requestId': req_id}
        
        response = getattr(requests, http_method)(
            url=url, 
            headers=headers, 
            data=data, 
            params=params
        )
        
        if request_timeout and response.status_code == 408:
            raise AssertionError(
                f"Error on API request --> http code: {response.status_code} message: {response.text}"
            )
        
        try:
            response_json = response.json()
            
            if 'status' in response_json and response_json['status'] != 'Ok':
                self.logger.error(response_json)
                print(response_json)
                error_message = response_json.get('message', response_json.get('status_message', 'Unknown error'))
                raise AssertionError(f'Unable to retrieve data: {error_message}')
                
        except JSONDecodeError:
            raise AssertionError(f'Error decoding the response for {url}')
        
        return response_json
    
    def retrieve_token(self) -> str:
        """
        Retrieve Bearer token for API authentication
        
        Returns:
            Bearer token string
        """
        ttid_headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'accept': 'application/json',
            'x-api-key': self.api_key
        }
        
        ttid_data = {
            'grant_type': 'user_app',
            'app_key': self.api_secret
        }
        
        token_url = f'{self.base_url}/ttid/{self.environment}/token'
        
        try:
            login_info = self.api_request(
                url=token_url,
                headers=ttid_headers,
                data=ttid_data,
                http_method='post',
                requires_auth=False,  # this IS the auth call — would recurse otherwise
            )
            self.logger.info(f"Token retrieved successfully")
        except AssertionError:
            raise
        
        token = f"{login_info['token_type'].capitalize()} {login_info['access_token']}"
        self.bearer_token = token
        # self.token_expiry = time.time() + login_info['seconds_until_expiry']
        
        return token
    
    def create_user(self) -> Dict:
        """
        Create a new TT user (requires HMAC authentication)
        
        Returns:
            User creation response
        """
        url_path = f'/ttuser/{self.environment}/user'
        new_user_url = self.base_url + url_path
        
        headers = self.generate_hmac_header(
            http_method='POST',
            url_path=url_path
        )
        
        try:
            # HMAC-authenticated, not bearer-token — don't let api_request
            # overwrite this Authorization header with a bearer token.
            response = self.api_request(new_user_url, headers, http_method='post', requires_auth=False)
            self.logger.info("User created successfully")
            return response
        except AssertionError:
            raise
    
    def get_all_accounts(self) -> List[Dict]:
        """
        Get all trading accounts.

        TT caps each accounts response at ~500 records: a response includes
        'lastPage' (bool) and, when it's False, a 'nextPageKey' that must be
        sent as a query param to fetch the next page. Loop until lastPage is
        True so a company with more accounts than fit on one page doesn't
        silently lose the rest.

        Returns:
            List of account dictionaries
        """
        accounts_url = f'{self.base_url}/ttaccount/{self.environment}/accounts/'

        headers = {
            'x-api-key': self.api_key,
            'Authorization': self.bearer_token
        }

        try:
            all_accounts = []
            next_page_key = None
            while True:
                params = {'nextPageKey': next_page_key} if next_page_key else {}
                account_map = self.api_request(accounts_url, headers, user_params=params)
                all_accounts.extend(account_map.get('accounts', []))

                # TT sends 'lastPage' as the string "true"/"false", not a
                # JSON boolean — a plain truthy check on the string "false"
                # would break the loop after the first page every time.
                last_page = str(account_map.get('lastPage', 'true')).lower() == 'true'
                if last_page:
                    break
                next_page_key = account_map.get('nextPageKey')
                if not next_page_key:
                    break

            self.accounts = all_accounts
            self.logger.info(f"Retrieved {len(self.accounts)} accounts")
            return self.accounts
        except AssertionError:
            raise
    
    def filter_accounts(self, account_names: List[str]) -> List[Dict]:
        """
        Filter accounts by name with smart suffix handling
        
        Rules:
        - If input contains underscore (e.g., "EE093_ASXOLD"), match exact pattern including suffix
        - If input has no underscore (e.g., "EE093"), match base account only (exclude any with underscores)
        - This handles any suffix dynamically (_ASXOLD, _HKEX, _DEMO, etc.)
        
        Args:
            account_names: List of account name substrings to match
            
        Returns:
            List of matching accounts
        """
        if not self.accounts:
            self.get_all_accounts()
        
        filtered_accounts = []
        
        for search_term in account_names:
            search_upper = search_term.upper()
            
            is_suffix_search = '_' in search_upper
            
            for account in self.accounts:
                account_name = account['name'].upper()
                
                if search_upper not in account_name:
                    continue
                
                if is_suffix_search:
                    filtered_accounts.append(account)
                else:
                    # User wants base account (no underscore) - exclude any account with underscore after the match
                    # Example: searching "EE093" should match "LGBEE093" but not "LGBEE093_ANYTHING"
                    
                    match_index = account_name.find(search_upper)
                    if match_index != -1:
                        after_match = account_name[match_index + len(search_upper):]
                        
                        if not after_match or not after_match.startswith('_'):
                            filtered_accounts.append(account)
        
        seen_ids = set()
        unique_accounts = []
        for account in filtered_accounts:
            if account['id'] not in seen_ids:
                seen_ids.add(account['id'])
                unique_accounts.append(account)
        
        return unique_accounts
    
    def login(self,  db_session: Session = None) -> Dict:
        """
        Complete login flow: retrieve token, create user (if needed), and fetch accounts
        
        Returns:
            Dictionary with login status and account information
        """
        try:
            # Step 1: Get bearer token
            self.logger.info("Step 1: Retrieving bearer token...")
            token = self.retrieve_token()
            
            # Step 2: Create user (optional - only if needed)
            # Uncomment if you need to create user on login
            # self.logger.info("Step 2: Creating user...")
            # user_response = self.create_user()
            
            # Step 3: Get all accounts
            self.logger.info("Step 2: Fetching accounts...")
            accounts = self.get_all_accounts()
            
            markets_sync_result = None
            accounts_sync_result = None
            if db_session:
                self.logger.info("Step 3: Syncing markets to database...")
                markets_sync_result = self.sync_markets_to_db(db_session)
                self.logger.info("Step 4: Syncing accounts to database...")
                accounts_sync_result = self.sync_all_accounts_to_db(db_session, tt_accounts=accounts)
            return {
                'status': 'success',
                'token': token,
                'accounts': accounts,
                'account_count': len(accounts),
                'markets_sync': markets_sync_result,
                'accounts_sync': accounts_sync_result
            }
            
        except Exception as e:
            self.logger.error(f"Login failed: {str(e)}")
            return {
                'status': 'error',
                'message': str(e)
            }

    def get_all_users(self) -> List[Dict]:
        """
        Get every TT user for this company (GET /ttuser/{env}/users) —
        resolves a fill's curr_user_id to a real name (alias/first/last
        name). Same 'lastPage'/'nextPageKey' pagination as get_all_accounts.
        """
        users_url = f'{self.base_url}/ttuser/{self.environment}/users'
        headers = {'x-api-key': self.api_key, 'Authorization': self.bearer_token}

        all_users = []
        next_page_key = None
        while True:
            params = {'nextPageKey': next_page_key} if next_page_key else {}
            user_map = self.api_request(users_url, headers, user_params=params)
            all_users.extend(user_map.get('users', []))

            last_page = str(user_map.get('lastPage', 'true')).lower() == 'true'
            if last_page:
                break
            next_page_key = user_map.get('nextPageKey')
            if not next_page_key:
                break

        self.logger.info(f"Retrieved {len(all_users)} users")
        return all_users

    def get_all_algos(self) -> List[Dict]:
        """
        Get every algo TT knows about for this company (GET
        /ttpds/{env}/algos) — resolves a fill's algo_id to a real name.
        """
        algos_url = f'{self.base_url}/ttpds/{self.environment}/algos'
        headers = {'x-api-key': self.api_key, 'Authorization': self.bearer_token}

        all_algos = []
        next_page_key = None
        while True:
            params = {'nextPageKey': next_page_key} if next_page_key else {}
            algo_map = self.api_request(algos_url, headers, user_params=params)
            all_algos.extend(algo_map.get('algos', []))

            last_page = str(algo_map.get('lastPage', 'true')).lower() == 'true'
            if last_page:
                break
            next_page_key = algo_map.get('nextPageKey')
            if not next_page_key:
                break

        self.logger.info(f"Retrieved {len(all_algos)} algos")
        return all_algos

    def get_positions(self):

        fill_url = f'{self.base_url}/ttmonitor/{self.environment}/position/'
        headers = {
            'x-api-key': self.api_key,
            'Authorization': self.bearer_token
        }
        
        try:
            fills_map = self.api_request(fill_url, headers)
            self.fills = fills_map.get('positions', [])
            self.logger.info(f"Retrieved {len(self.fills)} positions")
            return self.fills
        except AssertionError:
            raise

    def get_all_fills(self, start_time_ns=None, end_time_ns=None,accountID=None):

        headers = {
            "x-api-key": self.api_key,
            "Authorization": self.bearer_token,
            "Accept": "application/json"
        }

        fill_url = f"{self.base_url}/ttledger/{self.environment}/fills"

        all_fills = []
        current_min = start_time_ns
        counter = 1
        while True:
            print(counter)
            counter += 1
            params = {}
            
            if current_min is not None:
                params["minTimestamp"] = str(current_min)

            if end_time_ns is not None:
                params["maxTimestamp"] = str(end_time_ns)
            params["accountId"] = accountID
            response = self.api_request(
                fill_url,
                headers,
                user_params=params
            )

            fills = response.get("fills", [])

            if not fills:
                break

            all_fills.extend(fills)

            # Extract timestamp from last fill
            last_timestamp = int(fills[-1]["timeStamp"])

            # If fewer than 500 returned, we are done
            if len(fills) < 500:
                break

            # Move forward 1 nanosecond
            current_min = last_timestamp + 1

        self.logger.info(f"Retrieved {len(all_fills)} total fills")
        return all_fills


    def get_markets(self) -> List[Dict]:
        """
        Get all available markets/exchanges from TT
        
        Returns:
            List of market dictionaries
        """

        market_url = f'{self.base_url}/ttpds/{self.environment}/markets/'
        
        headers = {
            'x-api-key': self.api_key,
            'Authorization': self.bearer_token
        }
        
        try:
            response = self.api_request(market_url, headers)
            markets = response.get('markets', [])
            self.logger.info(f"Retrieved {len(markets)} markets")
            return markets
        except AssertionError:
            raise

    def sync_markets_to_db(self, db_session: Session) -> Dict:

        from database.db import Market  # Import here to avoid circular dependency
        
        try:
            tt_markets = self.get_markets()
            
            if not tt_markets:
                self.logger.warning("No markets retrieved from TT API")
                return {
                    'synced': 0,
                    'skipped': 0,
                    'total': 0
                }
            
            synced_count = 0
            skipped_count = 0
            
            for tt_market in tt_markets:
                # Check if market already exists
                existing = db_session.query(Market).filter(
                    Market.tt_market_id == tt_market['id']
                ).first()
                
                if existing:
                    skipped_count += 1
                    continue
                
                db_market = Market(
                    tt_market_id=tt_market['id'],
                    name=tt_market['name']
                )
                
                db_session.add(db_market)
                synced_count += 1
            
            db_session.commit()
            
            self.logger.info(f"Markets synced: {synced_count} new, {skipped_count} existing")
            
            return {
                'synced': synced_count,
                'skipped': skipped_count,
                'total': len(tt_markets)
            }
            
        except Exception as e:
            db_session.rollback()
            self.logger.error(f"Error syncing markets: {str(e)}")
            raise

    def sync_all_accounts_to_db(self, db_session: Session, tt_accounts: Optional[List[Dict]] = None) -> Dict:
        """
        Write-through cache of every account TT returns (full company, not
        just accounts added for PNL tracking) into TTAccountCache — upserted
        since fields like 'revision' change over time, unlike Market which
        is static.

        Args:
            tt_accounts: Already-fetched account list to avoid a redundant
                TT call (e.g. from login(), which fetches accounts anyway).
                Falls back to fetching from TT itself if not given.
        """
        from database.db import TTAccountCache  # Import here to avoid circular dependency

        try:
            if tt_accounts is None:
                tt_accounts = self.get_all_accounts()

            if not tt_accounts:
                self.logger.warning("No accounts retrieved from TT API")
                return {'synced': 0, 'updated': 0, 'total': 0}

            existing_by_id = {
                row.tt_account_id: row for row in db_session.query(TTAccountCache).all()
            }

            new_count = 0
            updated_count = 0

            for tt_account in tt_accounts:
                account_id = tt_account['id']
                existing = existing_by_id.get(account_id)

                if existing:
                    existing.account_type = tt_account['accountType']
                    existing.company_id = tt_account['companyId']
                    existing.name = tt_account['name']
                    existing.parent_account_id = tt_account.get('parentAccountId')
                    existing.parent_id = tt_account.get('parentId')
                    existing.revision = tt_account.get('revision')
                    existing.raw = tt_account
                    updated_count += 1
                else:
                    db_session.add(TTAccountCache(
                        tt_account_id=account_id,
                        account_type=tt_account['accountType'],
                        company_id=tt_account['companyId'],
                        name=tt_account['name'],
                        parent_account_id=tt_account.get('parentAccountId'),
                        parent_id=tt_account.get('parentId'),
                        revision=tt_account.get('revision'),
                        raw=tt_account,
                    ))
                    new_count += 1

            db_session.commit()

            self.logger.info(f"TT account cache synced: {new_count} new, {updated_count} updated")

            return {
                'synced': new_count,
                'updated': updated_count,
                'total': len(tt_accounts)
            }

        except Exception as e:
            db_session.rollback()
            self.logger.error(f"Error syncing accounts to cache: {str(e)}")
            raise

    def get_products(self, market_id: str = None, product_type_id: str = None) -> List[Dict]:
        """
        Get all products/instruments from TT
        
        Args:
            market_id:  market ID to filter products (e.g., "7" for CME)
            product_type_id: Optional product type ID to filter (e.g., "1" for futures)
            
        Returns:
            List of product dictionaries
        """

        products_url = f'{self.base_url}/ttpds/{self.environment}/products'
        
        headers = {
            'x-api-key': self.api_key,
            'Authorization': self.bearer_token
        }
        
        params = {}
        if market_id:
            params['marketId'] = market_id
        if product_type_id:
            params['productTypeId'] = product_type_id
        
        try:
            response = self.api_request(products_url, headers, user_params=params)
            products = response.get('products', [])
            self.logger.info(f"Retrieved {len(products)} products")
            print(products)
            return products
        except AssertionError:
            raise

    def get_instrument_by_id(self, instrument_id: str) -> Dict:
        """
        Get instrument details with extracted key fields for easy use
        
        Args:
            instrument_id: TT instrument ID
            
        Returns:
            Simplified instrument dictionary with essential fields
        """

        instrument_url = f'{self.base_url}/ttpds/{self.environment}/instrument/{instrument_id}'
        
        headers = {
            'x-api-key': self.api_key,
            'Authorization': self.bearer_token
        }
        
        try:
            response = self.api_request(instrument_url, headers)
            
            return response["instrument"][0]
            
        except AssertionError as e:
            self.logger.error(f"Failed to retrieve instrument {instrument_id}: {str(e)}")
            raise
    #response
    # {'instrument': [{'alias': 'IR Dec27', 'displayFactor': 1, 'displayType': 0, 'expirationDate': 20271209235959, 'id': '6867822116513465051', 'lastTradeDate': 20271209, 'marketId': 24, 'name': 'IRZ7', 'pointValue': 2415.0, 'productFamilyId': '854368379367750817', 'productId': '2133604359106408858', 'productSymbol': 'IR', 'productTypeId': 34, 'ricCode': 'YBAZ7', 'roundLotQty': 1, 'securityExchange': 40, 'securityId': '271377', 'seriesTermId': 4, 'term': 'Dec27', 'tickSize': 0.01, 'tickSizeDenominator': 1000, 'tickSizeNumerator': 10, 'tickValue': 24.15}], 'lastPage': 'true', 'status': 'Ok'}

    def get_product_by_id(self, product_id: str) -> Dict:
        """Get product details (includes currencyId) for a given TT product ID"""

        product_url = f'{self.base_url}/ttpds/{self.environment}/product/{product_id}'

        headers = {
            'x-api-key': self.api_key,
            'Authorization': self.bearer_token
        }

        try:
            response = self.api_request(product_url, headers)
            if response.get('status') == "Fail":
                return {}
            return response.get("product", {})
        except AssertionError as e:
            self.logger.error(f"Failed to retrieve product {product_id}: {str(e)}")
            raise

    def get_usd_rate(self, from_currency_id: str) -> Optional[float]:
        """Get the conversion rate from a TT currency ID to USD (currency id 151)"""

        if str(from_currency_id) == "151":
            return 1.0

        cur_url = f'{self.base_url}/ttpds/{self.environment}/currencyrates/'

        headers = {
            'x-api-key': self.api_key,
            'Authorization': self.bearer_token
        }
        user_params = {"fromCurrencyId": from_currency_id, "toCurrencyId": "151"}

        try:
            response = self.api_request(cur_url, headers, user_params=user_params)
            rate = response.get("currency_rate", {}).get("rate")
            return float(rate) if rate else None
        except AssertionError as e:
            self.logger.error(f"Failed to retrieve currency rate for {from_currency_id}: {str(e)}")
            raise

from datetime import datetime, timezone
from zoneinfo import ZoneInfo  

def convert_ns_to_time(ns_timestamp):
    if ns_timestamp is None:
        return None

    # Convert nanoseconds to seconds (float keeps precision)
    seconds = int(ns_timestamp) / 1_000_000_000

    # Create UTC datetime
    dt_utc = datetime.fromtimestamp(seconds, tz=timezone.utc)

    # Convert to IST
    dt_ist = dt_utc.astimezone(ZoneInfo("Asia/Kolkata"))

    # Return milliseconds precision
    return dt_ist.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]    


def convert_time_to_ns(timestamp_str):
    if timestamp_str is None:
        return None
    
    # Parse the input timestamp (assumed to be in UTC)
    dt = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S.%f')
    
    # Make the datetime object timezone-aware (assuming UTC)
    dt = dt.replace(tzinfo=timezone.utc)

    # Convert to nanoseconds since epoch
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    ns_timestamp = int((dt - epoch).total_seconds() * 10**9)

if __name__ == "__main__":
    # Initialize client
    API_KEY = '55e5902f-199e-7904-c71f-a1a965cabdd4'
    API_SECRET = '55e5902f-199e-7904-c71f-a1a965cabdd4:3bb82cd5-3527-1e91-8209-253d280a6d18'
    
    client = TTClient(
        api_key=API_KEY,
        api_secret=API_SECRET,
        environment='ext_prod_live'
    )
    
    # Login
    login_result = client.login()
    print(f"Login Status: {login_result['status']}")

    #instrument id
    # instru = client.get_instrument_by_id("11621744175441882652")
    # print(instru)
    # client.get_products(market_id="7")


    # Check fills
    # start_time_ns = convert_time_to_ns('2026-02-11 03:30:00.000')
    # end_time_ns = convert_time_to_ns('2026-02-12 03:30:00.000')
    # fills = client.get_all_fills(start_time_ns=start_time_ns, end_time_ns=end_time_ns, accountID="1324415")
    # print(f"Total Fills: {len(fills)}") 
    # # print(f"Sample Fill: {fills[-1] if fills else 'No fills found'}")
    # df = pd.DataFrame(fills)
    # df['utc_time'] = df['transactTime'].apply(convert_ns_to_time)
    # df.to_csv("../fills.csv")
    # Check accounts
    # print(f"Total Accounts: {login_result.get('account_count', 0)}")
    
    # Filter specific accounts
    # filtered = client.filter_accounts(['EE816'])
    # print(f"\nFiltered Accounts: {filtered}")