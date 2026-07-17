import asyncio
import json
import logging
import websockets
import config as config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("DataFeed")

class DerivDataFeed:
    def __init__(self, pairs=None, callback=None):
        self.pairs = pairs or config.MONITORED_PAIRS
        self.callback = callback  # Function to call when a candle completes: callback(pair, df)
        self.candles_history = {pair: [] for pair in self.pairs}
        self.websocket = None
        self.running = False

    async def connect_and_subscribe(self):
        logger.info(f"Connecting to Deriv WS: {config.DERIV_WS_URL}")
        try:
            async with websockets.connect(config.DERIV_WS_URL) as ws:
                self.websocket = ws
                self.running = True
                
                # Subscribe to each pair
                for pair in self.pairs:
                    subscribe_request = {
                        "ticks_history": pair,
                        "adjust_start_time": 1,
                        "count": 200,
                        "end": "latest",
                        "start": 1,
                        "style": "candles",
                        "granularity": 300,  # 5 minutes in seconds
                        "subscribe": 1
                    }
                    await ws.send(json.dumps(subscribe_request))
                    logger.info(f"Sent subscription request for {pair}")
                    await asyncio.sleep(0.5) # Prevent flooding

                # Start listening to messages
                await self.receive_messages()
        except Exception as e:
            logger.error(f"WebSocket Connection error: {e}")
            self.running = False
            
    async def receive_messages(self):
        while self.running:
            try:
                message = await self.websocket.recv()
                data = json.loads(message)
                
                # Check for errors
                if "error" in data:
                    logger.error(f"Error from Deriv API: {data['error']['message']}")
                    continue

                # Identify which pair this message belongs to
                pair = data.get("echo_req", {}).get("ticks_history")
                if not pair or pair not in self.pairs:
                    continue

                # Case 1: Initial list of historical candles
                if "candles" in data:
                    logger.info(f"Received historical candles for {pair} ({len(data['candles'])} candles)")
                    self.candles_history[pair] = data["candles"]
                
                # Case 2: Real-time active candle update (Deriv pushes OHLC subscription updates)
                elif "ohlc" in data:
                    ohlc = data["ohlc"]
                    # Format string values to floats/ints to match the historical candles structure
                    # We use 'open_time' as the epoch because 'epoch' in real-time push represents the tick time, not the candle start time.
                    candle = {
                        "open": float(ohlc["open"]),
                        "high": float(ohlc["high"]),
                        "low": float(ohlc["low"]),
                        "close": float(ohlc["close"]),
                        "epoch": int(ohlc["open_time"]),
                        "volume": float(ohlc.get("volume", 1.0))
                    }
                    history = self.candles_history[pair]
                    
                    if not history:
                        # If history is empty, initialize it
                        history.append(candle)
                        continue

                    last_candle = history[-1]
                    
                    # If it's the same candle (matching epoch), update it
                    if candle["epoch"] == last_candle["epoch"]:
                        history[-1] = candle
                    # If it's a new candle (newer epoch), append it
                    elif candle["epoch"] > last_candle["epoch"]:
                        logger.info(f"New 5m candle started for {pair}. Previous candle closed at {last_candle['close']}")
                        
                        # Append the new open candle
                        history.append(candle)
                        
                        # Keep history size clean
                        if len(history) > 100:
                            history.pop(0)
                        
                        # Invoke callback: A candle has just completed!
                        # The completed candle is now history[-2] (the second to last)
                        if self.callback:
                            # Run async callback
                            asyncio.create_task(self.callback(pair, history))
                            
            except websockets.exceptions.ConnectionClosed:
                logger.warning("Deriv WebSocket connection closed. Reconnecting...")
                break
            except Exception as e:
                logger.error(f"Error parsing WebSocket message: {e}")
                await asyncio.sleep(1)

    async def run(self):
        while True:
            await self.connect_and_subscribe()
            logger.info("Retrying connection in 5 seconds...")
            await asyncio.sleep(5)

async def fetch_1m_candles(pair: str) -> list:
    """
    Performs a one-shot WebSocket request to fetch the last 5 1-minute candles.
    """
    logger.info(f"Fetching 1m candles for {pair}...")
    try:
        async with websockets.connect(config.DERIV_WS_URL) as ws:
            request = {
                "ticks_history": pair,
                "adjust_start_time": 1,
                "count": 5,
                "end": "latest",
                "start": 1,
                "style": "candles",
                "granularity": 60  # 1 minute in seconds
            }
            await ws.send(json.dumps(request))
            response = await ws.recv()
            data = json.loads(response)
            
            if "error" in data:
                logger.error(f"Error fetching 1m candles: {data['error']['message']}")
                return []
                
            return data.get("candles", [])
    except Exception as e:
        logger.error(f"Failed to fetch 1m candles: {e}")
        return []

async def fetch_h1_candles(pair: str, count: int = 250) -> list:
    """
    Performs a one-shot WebSocket request to fetch historical 1-hour candles.
    Used for H1 Trend Filter (EMA 50 vs EMA 200).
    """
    logger.info(f"Fetching {count} H1 candles for {pair}...")
    try:
        async with websockets.connect(config.DERIV_WS_URL) as ws:
            request = {
                "ticks_history": pair,
                "adjust_start_time": 1,
                "count": count,
                "end": "latest",
                "start": 1,
                "style": "candles",
                "granularity": 3600  # 1 hour in seconds
            }
            await ws.send(json.dumps(request))
            response = await ws.recv()
            data = json.loads(response)
            
            if "error" in data:
                logger.error(f"Error fetching H1 candles: {data['error']['message']}")
                return []
                
            return data.get("candles", [])
    except Exception as e:
        logger.error(f"Failed to fetch H1 candles: {e}")
        return []
