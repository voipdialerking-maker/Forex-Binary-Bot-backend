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

async def _fetch_tiingo(pair: str, interval: str, count: int) -> list:
    """Internal function to perform the actual HTTP request to Tiingo."""
    ticker = get_tiingo_ticker(pair)
    
    # Tiingo uses 1min, 5min, 15min
    tiingo_freq = interval.replace("m", "min")
    
    url = config.TIINGO_API_URL
    params = {
        "tickers": ticker,
        "resampleFreq": tiingo_freq,
        "token": config.TIINGO_TOKEN,
    }
    
    try:
        # Run synchronous requests.get in a separate thread so it doesn't block the asyncio event loop
        response = await asyncio.to_thread(requests.get, url, params=params, timeout=10)
        
        if response.status_code != 200:
            logger.error(f"Tiingo API Error {response.status_code}: {response.text}")
            return []
            
        data = response.json()
        if not data:
            return []
            
        # Format Tiingo response to match Deriv's OHLC format
        # Deriv format: {"open": 1.1, "high": 1.2, "low": 1.0, "close": 1.15, "epoch": 1234567890, "volume": 1.0}
        formatted_candles = []
        for candle in data:
            # Tiingo returns date as "2026-07-23T00:00:00.000Z"
            dt = datetime.strptime(candle["date"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
            epoch = int(dt.timestamp())
            
            formatted_candles.append({
                "open": float(candle["open"]),
                "high": float(candle["high"]),
                "low": float(candle["low"]),
                "close": float(candle["close"]),
                "epoch": epoch,
                "volume": 1.0  # Tiingo Forex doesn't provide volume, default to 1.0
            })
            
        # Return the last 'count' candles
        return formatted_candles[-count:]
        
    except Exception as e:
        logger.error(f"Failed to fetch data from Tiingo for {pair}: {e}")
        return []

async def fetch_tiingo_candles_cached(pair: str, interval: str, count: int) -> list:
    """
    Fetches candles from Tiingo with caching for higher timeframes (5m, 15m) to respect rate limits.
    1m candles are fetched fresh every time (as they change every minute).
    """
    current_time = time.time()
    
    # Initialize cache for this pair if not exists
    if pair not in _CACHE:
        _CACHE[pair] = {}
        
    if interval not in _CACHE[pair]:
        _CACHE[pair][interval] = {"data": [], "last_fetched": 0}
        
    # Determine cache duration based on interval
    cache_duration = 0
    if interval == "5m":
        cache_duration = 300  # 5 minutes in seconds
    elif interval == "15m":
        cache_duration = 900  # 15 minutes in seconds
        
    cache_entry = _CACHE[pair][interval]
    
    # If the cache is still valid, return the cached data
    if current_time - cache_entry["last_fetched"] < cache_duration and cache_entry["data"]:
        return cache_entry["data"]
        
    # Otherwise, fetch fresh data
    data = await _fetch_tiingo(pair, interval, count)
    
    if data:
        # Update cache
        cache_entry["data"] = data
        cache_entry["last_fetched"] = current_time
        logger.info(f"Fetched fresh {interval} data from Tiingo for {pair}.")
        
    return cache_entry["data"]
