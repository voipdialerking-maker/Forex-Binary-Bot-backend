import asyncio
import requests
import logging
import time
from datetime import datetime, timezone
import config

logger = logging.getLogger("TiingoClient")

# Cache to store historical data for higher timeframes (5m, 15m)
# Structure: { "pair": { "5m": {"data": [], "last_fetched": timestamp}, ... } }
_CACHE = {}

def get_tiingo_ticker(deriv_pair: str) -> str:
    """Converts a Deriv pair (e.g. frxEURUSD) to Tiingo ticker (e.g. eurusd)"""
    return deriv_pair.replace("frx", "").lower()

def get_deriv_pair(tiingo_ticker: str) -> str:
    """Converts a Tiingo ticker back to a Deriv pair"""
    return "frx" + tiingo_ticker.upper()

async def fetch_multiple_tiingo_candles(pairs: list, interval: str, count: int) -> dict:
    """
    Fetches candles for MULTIPLE pairs in a single API request to save rate limits.
    Returns a dictionary mapping Deriv pairs to their respective candle arrays.
    """
    if not pairs:
        return {}
        
    tickers = ",".join([get_tiingo_ticker(p) for p in pairs])
    tiingo_freq = interval.replace("m", "min")
    
    url = config.TIINGO_API_URL
    params = {
        "tickers": tickers,
        "resampleFreq": tiingo_freq,
        "token": config.TIINGO_TOKEN,
    }
    
    try:
        response = await asyncio.to_thread(requests.get, url, params=params, timeout=15)
        
        if response.status_code != 200:
            logger.error(f"Tiingo API Error {response.status_code}: {response.text}")
            return {}
            
        data = response.json()
        if not data:
            return {}
            
        # Group data by ticker
        results = {p: [] for p in pairs}
        
        for item in data:
            ticker = item.get("ticker", "")
            if not ticker:
                continue
                
            pair = get_deriv_pair(ticker)
            if pair not in results:
                continue
                
            dt = datetime.strptime(item["date"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
            epoch = int(dt.timestamp())
            
            results[pair].append({
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"]),
                "epoch": epoch,
                "volume": 1.0
            })
            
        # Limit each array to the requested count
        for pair in results:
            results[pair] = results[pair][-count:]
            
        return results
        
    except Exception as e:
        logger.error(f"Failed to fetch bulk data from Tiingo: {e}")
        return {}

async def fetch_tiingo_candles_cached(pair: str, interval: str, count: int) -> list:
    """
    Fetches candles from Tiingo with caching for higher timeframes (5m, 15m) to respect rate limits.
    If the cache is empty, it fetches fresh data for this single pair.
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
        
    # Fetch fresh data (using the bulk function but with a list of 1)
    results = await fetch_multiple_tiingo_candles([pair], interval, count)
    data = results.get(pair, [])
    
    if data:
        cache_entry["data"] = data
        cache_entry["last_fetched"] = current_time
        logger.info(f"Fetched fresh {interval} data from Tiingo for {pair}.")
        
    return cache_entry["data"]
