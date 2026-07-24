import logging
import asyncio
from datetime import datetime
import tv_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("DataFeed")

class TVDataFeed:
    """
    Primary Data Feed using TradingView (tvDatafeed).
    Polls 1-minute candles for all configured pairs sequentially.
    """
    def __init__(self, pairs: list, callback=None):
        self.pairs = pairs
        self.callback = callback
        self.running = False
        
        # Keep an internal history of the last 250 candles for each pair
        self.candles_history = {p: [] for p in pairs}

    async def _fetch_initial_history(self):
        """Fetches the initial 250 candles for all pairs on startup."""
        logger.info("Fetching initial 250 candles for all pairs from TradingView...")
        results = await tv_client.fetch_multiple_tv_candles(self.pairs, "1m", 250)
        
        for pair, candles in results.items():
            if candles:
                self.candles_history[pair] = candles
                logger.info(f"Loaded {len(candles)} historical candles for {pair}")
            else:
                logger.error(f"Failed to load historical candles for {pair}")

    async def _polling_loop(self):
        """Main loop that polls TradingView every 60 seconds."""
        while self.running:
            # Sleep until the next minute boundary (approximate)
            now = datetime.now()
            seconds_until_next_minute = 60 - now.second
            await asyncio.sleep(seconds_until_next_minute)
            
            if not self.running:
                break
                
            logger.debug("Polling TradingView for 1m candles...")
            
            # Fetch the latest 2 candles for all pairs
            results = await tv_client.fetch_multiple_tv_candles(self.pairs, "1m", 2)
            
            for pair, candles in results.items():
                if not candles:
                    continue
                    
                history = self.candles_history[pair]
                
                for c in candles:
                    # Check if we already have this candle
                    if not history or c['epoch'] > history[-1]['epoch']:
                        history.append(c)
                        
                        # Keep history size clean
                        if len(history) > 250:
                            history.pop(0)
                            
                        # If callback is registered, trigger it with the updated history
                        if self.callback:
                            asyncio.create_task(self.callback(pair, list(history), source="tradingview"))
                            
            # Add a small buffer sleep so we don't double-fire in the same second
            await asyncio.sleep(1)

    async def run(self):
        self.running = True
        logger.info("Starting TradingView Primary Data Feed...")
        
        # 1. Fetch initial history
        await self._fetch_initial_history()
        
        # 2. Start polling loop
        await self._polling_loop()

    async def stop(self):
        self.running = False
        logger.info("Stopping TradingView Data Feed...")
