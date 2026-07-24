import asyncio
import logging
import time
import pandas as pd
from datetime import datetime, timezone
from tvDatafeed import TvDatafeed, Interval

logger = logging.getLogger("TVClient")

# Cache to store historical data for higher timeframes (5m, 15m)
# Structure: { "pair": { "5m": {"data": [], "last_fetched": timestamp}, ... } }
_CACHE = {}

# Initialize tvDatafeed (without login)
try:
    tv = TvDatafeed()
    logger.info("tvDatafeed initialized successfully.")
except Exception as e:
    logger.error(f"Failed to initialize tvDatafeed: {e}")
    tv = None

def get_tv_symbol(deriv_pair: str) -> str:
    """Converts a Deriv pair (e.g. frxEURUSD) to TV symbol (e.g. EURUSD)"""
    return deriv_pair.replace("frx", "").upper()

def get_deriv_pair(tv_symbol: str) -> str:
    """Converts a TV symbol back to a Deriv pair"""
    return "frx" + tv_symbol.upper()

async def fetch_multiple_tv_candles(pairs: list, interval: str, count: int) -> dict:
    """
    Fetches candles for MULTIPLE pairs sequentially (since tvDatafeed fetches one by one).
    Returns a dictionary mapping Deriv pairs to their respective candle arrays.
    """
    if not pairs or tv is None:
        return {}
        
    tv_interval = Interval.in_1_minute
    if interval == "5m":
        tv_interval = Interval.in_5_minute
    elif interval == "15m":
        tv_interval = Interval.in_15_minute

    results = {p: [] for p in pairs}
    
    for pair in pairs:
        symbol = get_tv_symbol(pair)
        
        try:
            # tvDatafeed uses synchronous requests, so we wrap it in a thread
            # Try OANDA first, if it fails try FXCM
            def fetch_data():
                df = tv.get_hist(symbol=symbol, exchange='OANDA', interval=tv_interval, n_bars=count)
                if df is None or df.empty:
                    df = tv.get_hist(symbol=symbol, exchange='FXCM', interval=tv_interval, n_bars=count)
                return df

            df = await asyncio.to_thread(fetch_data)
            
            if df is not None and not df.empty:
                # Format to match our OHLC format
                formatted_candles = []
                for dt, row in df.iterrows():
                    # df index is datetime (usually local to the system running it)
                    if dt.tzinfo is None:
                        # Convert naive to UTC assuming it is local time (tvDatafeed returns local time by default)
                        dt = dt.tz_localize(None).replace(tzinfo=timezone.utc)
                        
                    epoch = int(dt.timestamp())
                    
                    formatted_candles.append({
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "epoch": epoch,
                        "volume": float(row["volume"])
                    })
                    
                results[pair] = formatted_candles
            else:
                logger.warning(f"No data returned for {pair} from TradingView.")
                
        except Exception as e:
            logger.error(f"Failed to fetch data for {pair} from TV: {e}")
            
    return results

async def fetch_tv_candles_cached(pair: str, interval: str, count: int) -> list:
    """
    Fetches candles from TV with caching for higher timeframes (5m, 15m).
    """
    current_time = time.time()
    
    if pair not in _CACHE:
        _CACHE[pair] = {}
        
    if interval not in _CACHE[pair]:
        _CACHE[pair][interval] = {"data": [], "last_fetched": 0}
        
    cache_duration = 0
    if interval == "5m":
        cache_duration = 300
    elif interval == "15m":
        cache_duration = 900
        
    cache_entry = _CACHE[pair][interval]
    
    if current_time - cache_entry["last_fetched"] < cache_duration and cache_entry["data"]:
        return cache_entry["data"]
        
    # Fetch fresh data
    results = await fetch_multiple_tv_candles([pair], interval, count)
    data = results.get(pair, [])
    
    if data:
        cache_entry["data"] = data
        cache_entry["last_fetched"] = current_time
        logger.info(f"Fetched fresh {interval} data from TV for {pair}.")
        
    return cache_entry["data"]
