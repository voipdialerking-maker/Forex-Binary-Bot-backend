import asyncio
import json
import logging
import websockets

logger = logging.getLogger("TiingoWS")

# Global dictionary to store the latest live prices
LATEST_PRICES = {}

async def tiingo_websocket_loop():
    url = "wss://api.tiingo.com/fx"
    subscribe_msg = {
        "eventName": "subscribe",
        "authorization": "6d5442a6595792eed12d7371665df2190ade68fe",
        "eventData": {
            "thresholdLevel": 5,
            "tickers": ["eurusd", "gbpusd", "audusd", "usdjpy", "eurjpy", "gbpjpy"]
        }
    }
    
    while True:
        try:
            logger.info("Connecting to Tiingo WebSocket for Live Prices...")
            async with websockets.connect(url) as ws:
                await ws.send(json.dumps(subscribe_msg))
                
                while True:
                    msg = await ws.recv()
                    data = json.loads(msg)
                    
                    # MessageType 'A' means a quote update
                    if data.get("messageType") == "A" and "data" in data and len(data["data"]) > 2:
                        ticker = str(data["data"][1]).upper()
                        symbol = f"frx{ticker}"
                        
                        # Find the first number from the end of the array (which is usually the mid/bid price)
                        price = next((v for v in reversed(data["data"]) if isinstance(v, (int, float))), None)
                        
                        if price:
                            LATEST_PRICES[symbol] = price
                            
        except Exception as e:
            logger.error(f"Tiingo WebSocket error: {e}. Reconnecting in 5 seconds...")
            await asyncio.sleep(5)
