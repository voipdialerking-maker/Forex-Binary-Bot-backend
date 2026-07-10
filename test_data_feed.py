import asyncio
import json
import logging
import websockets
import config as config
from indicators import calculate_all_indicators
from strategy import validate_1m_exhaustion
from data_feed import fetch_1m_candles

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("TestRunner")

async def test_full_validation_oneshot():
    logger.info(f"Connecting to Deriv WS for One-Shot Verification: {config.DERIV_WS_URL}")
    try:
        async with websockets.connect(config.DERIV_WS_URL) as ws:
            # 1. Test 5-minute candles indicator calculation
            request = {
                "ticks_history": "frxEURUSD",
                "adjust_start_time": 1,
                "count": 200,
                "end": "latest",
                "start": 1,
                "style": "candles",
                "granularity": 300
            }
            await ws.send(json.dumps(request))
            logger.info("Sent request for frxEURUSD 5m candles...")
            
            response = await ws.recv()
            data = json.loads(response)
            
            if "error" in data:
                logger.error(f"Error from Deriv: {data['error']['message']}")
                return
                
            candles = data.get("candles", [])
            logger.info(f"Received {len(candles)} candles. Calculating 5m indicators...")
            df = calculate_all_indicators(candles)
            last_candle = df.iloc[-1]
            
            print("\n" + "="*50)
            print(f"5-MINUTE CHARTS INDICATORS RESULTS (frxEURUSD)")
            print(f"Close Price: {last_candle['close']:.5f}")
            print(f"RSI (14):      {last_candle['rsi']:.2f}")
            print(f"Stoch %K:     {last_candle['stoch_k']:.2f}")
            print(f"BB Upper:     {last_candle['bb_upper']:.5f} | Lower: {last_candle['bb_lower']:.5f}")
            print("="*50 + "\n")
            
        # 2. Test 1-minute candles fetching and Concept 2 validation
        candles_1m = await fetch_1m_candles("frxEURUSD")
        if candles_1m:
            print("="*50)
            print(f"1-MINUTE INTERNALS (Last 5 Candles for frxEURUSD)")
            for idx, c in enumerate(candles_1m):
                body = abs(float(c['close']) - float(c['open']))
                range_ch = float(c['high']) - float(c['low'])
                print(f"Candle {idx+1} [Epoch: {c['epoch']}]: Open: {c['open']}, Close: {c['close']}, Body Size: {body:.6f}, Range: {range_ch:.6f}")
            print("-"*50)
            
            print("Running Mock CALL validation:")
            call_passed = validate_1m_exhaustion(candles_1m, "CALL")
            
            print("\nRunning Mock PUT validation:")
            put_passed = validate_1m_exhaustion(candles_1m, "PUT")
            print("="*50 + "\n")
            
    except Exception as e:
        logger.error(f"Error running validation test: {e}")

if __name__ == "__main__":
    asyncio.run(test_full_validation_oneshot())
