import asyncio
import logging
from datetime import datetime, timezone
import http.server
import threading
import os
import sys
import pandas as pd
# Add current directory to path to support direct imports in all environments
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import config
import database
import notifier
from indicators import calculate_all_indicators
from strategy import check_trend_exhaustion, check_smc_sweep, check_sma_smc_strategy, validate_1m_exhaustion, check_m15_trend, check_vsa_scalp_strategy, check_master_candle_strategy, check_rsi_pivot_divergence_strategy, check_order_block_retest_strategy, check_mtf_smc_sniper_strategy, check_smt_divergence_sniper, check_master_dollar_compass_allow, check_5m_harami_smc_sniper
from data_feed import TVDataFeed

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Main")

# Track active signals in memory to check outcomes
# Format: { pair: [ { id: str, type: str, entry_price: float, expiry_epoch: int } ] }
active_signals_tracker = {pair: [] for pair in config.MONITORED_PAIRS}

# Track the last evaluated 5m candle epoch per pair to prevent duplicate executions from race conditions
last_processed_epoch = {pair: 0 for pair in config.MONITORED_PAIRS}

def format_pair_display(pair: str) -> str:
    """Converts frxEURUSD -> EUR/USD"""
    if pair.startswith("frx") and len(pair) == 9:
        return f"{pair[3:6]}/{pair[6:]}"
    return pair

async def handle_candle_completed(pair: str, candle_history: list, source: str = "tradingview"):
    """
    Callback triggered when a 1-minute candle closes.
    """
    try:
        # 1. Convert to DataFrame and calculate indicators
        df = calculate_all_indicators(candle_history)
        
        # Get details of the candle that just completed (index -2)
        completed_candle = df.iloc[-2]
        completed_epoch = int(completed_candle['epoch'])
        completed_close = float(completed_candle['close'])
        
        # Prevent race conditions or duplicate execution for the same completed candle close
        if completed_epoch <= last_processed_epoch[pair]:
            return
        last_processed_epoch[pair] = completed_epoch
        
        # 2. Check for active signals that need outcome evaluation
        await evaluate_pending_outcomes(pair, completed_epoch, completed_close)

        # 3. Check active trade lock
        if len(active_signals_tracker[pair]) > 0:
            logger.info(f"Signal evaluation skipped for {format_pair_display(pair)}: An active trade is already running.")
            return

        signal_data = None
        
        # Calculate exactly when the finalized 1m candle closed
        # completed_epoch is the START time of the 1m candle, so it closes 60 seconds later.
        candle_close_time = datetime.fromtimestamp(completed_epoch + 60, timezone.utc)
        candle_close_minute = candle_close_time.minute

        # Set up dynamic fetchers based on data source
        from tv_client import fetch_tv_candles_cached
        fetch_5m = lambda p, count=120: fetch_tv_candles_cached(p, "5m", count)
        fetch_m15 = lambda p, count=250: fetch_tv_candles_cached(p, "15m", count)
        fetch_1h = lambda p, count=50: fetch_tv_candles_cached(p, "1h", count)

        # -------------------------------------------------------------
        # Evaluate 5-Minute Institutional Strategies every 5th minute (5m Chart -> Next 5m Candle Expiry)
        # -------------------------------------------------------------
        if candle_close_minute % 5 == 0:
            logger.info(f"[{format_pair_display(pair)}] 5-Minute boundary reached (Close Time: {candle_close_time.strftime('%H:%M:%S UTC')}). Checking 5m Institutional Harami & OB Retest...")
            candles_5m = await fetch_5m(pair)
            if candles_5m and len(candles_5m) > 40:
                signal_data = check_5m_harami_smc_sniper(pair, candles_5m)
                if not signal_data:
                    df_5m = pd.DataFrame(candles_5m)
                    df_with_indicators = calculate_all_indicators(df_5m)
                    signal_data = check_order_block_retest_strategy(df_with_indicators)
        
        # -------------------------------------------------------------
        # Evaluate Strategy 10: Institutional SMT Divergence Sniper (#1 Priority EVERY minute)
        # -------------------------------------------------------------
        if not signal_data and any(p in pair.upper() for p in ["GBP/USD", "AUD/USD"]):
            eurusd_1m = await fetch_tv_candles_cached("EUR/USD", "1m", len(candle_history))
            if eurusd_1m and len(eurusd_1m) > 40:
                signal_data = check_smt_divergence_sniper(pair, candle_history, eurusd_1m)

        # -------------------------------------------------------------
        # Evaluate Strategy 9: Institutional MTF SMC Sniper (#2 Priority EVERY minute)
        # -------------------------------------------------------------
        if not signal_data:
            candles_1h_sniper = await fetch_1h(pair, count=50)
            candles_m15_sniper = await fetch_m15(pair, count=50)
            signal_data = check_mtf_smc_sniper_strategy(candles_1h_sniper, candles_m15_sniper, candle_history)

        if signal_data and "Harami" not in signal_data.get("strategy_name", ""):
            # --- INSTITUTIONAL MASTER DOLLAR COMPASS SHIELD ---
            eurusd_1h_shield = await fetch_1h("EUR/USD", count=50)
            eurusd_15m_shield = await fetch_m15("EUR/USD", count=50)
            if not check_master_dollar_compass_allow(pair, signal_data["signal"], eurusd_1h_shield, eurusd_15m_shield):
                logger.info(f"🚫 Trade discarded for {pair}: Counter-trend against Master EUR/USD Institutional Compass!")
                signal_data = None

        if signal_data:
            direction = signal_data["signal"]
            entry_price = signal_data["entry_price"]
            rsi = signal_data["rsi"]
            stochastic = signal_data["stochastic"]
            volume_ratio = signal_data["volume_ratio"]
            strategy_name = signal_data["strategy_name"]
            
            pair_display = format_pair_display(pair)
            
            # For Trend Exhaustion, we do extra H1 trend and 1m validation.
            # SMC Sweep already did its own validation inside strategy.py.
            if strategy_name == "Trend Exhaustion":
                logger.info(f"🚨 POTENTIAL SETUP: {pair_display} -> {direction} @ {entry_price}. Validating M15 Trend...")
                
                # Fetch M15 candles for Trend Filter
                candles_m15_trend = await fetch_m15(pair)
                if not check_m15_trend(candles_m15_trend, direction):
                    logger.info(f"❌ Setup discarded for {pair_display}: M15 Trend validation failed.")
                    return

                logger.info(f"✅ M15 Trend validated. Fetching 1m candles for exhaustion check...")

                # Fetch 1-minute candles for Concept 2 validation
                candles_1m_exhaustion = candle_history
                if not validate_1m_exhaustion(candles_1m_exhaustion, direction):
                    logger.info(f"❌ Setup discarded for {pair_display}: 1m exhaustion validation failed.")
                    return
            
            # --- SIGNAL CONFIRMED ---
            logger.info(f"🚀 SIGNAL CONFIRMED: {pair_display} - {direction} ({strategy_name})")

            # Send signal notification to Telegram
            notifier.send_telegram_signal(
                pair_display=pair_display,
                direction=direction,
                entry_price=entry_price,
                rsi=rsi,
                stochastic=stochastic,
                volume=volume_ratio,
                strategy=strategy_name
            )

            # Insert signal into Supabase DB
            signal_id = database.insert_signal(
                pair=pair,
                direction=direction,
                entry_price=entry_price,
                rsi=rsi,
                stochastic=stochastic,
                volume=volume_ratio,
                strategy=strategy_name
            )

            if signal_id:
                # Add to active signals tracker to evaluate outcome after 5 minutes (next candle close)
                # Expiry epoch is current candle epoch + 300 seconds (5 mins)
                expiry_epoch = completed_epoch + 300
                active_signals_tracker[pair].append({
                    "id": signal_id,
                    "type": direction,
                    "entry_price": entry_price,
                    "expiry_epoch": expiry_epoch
                })
                logger.info(f"Signal added to tracker. Awaiting expiry at epoch: {expiry_epoch}")
                
    except Exception as e:
        logger.error(f"Error handling candle completion for {pair}: {e}", exc_info=True)

async def evaluate_pending_outcomes(pair: str, completed_epoch: int, completed_close: float):
    """
    Evaluates tracked signals that have expired.
    """
    pair_display = format_pair_display(pair)
    pending = active_signals_tracker[pair]
    remaining = []

    for signal in pending:
        # Check if the completed candle epoch matches or has passed the expiry epoch
        if completed_epoch >= signal["expiry_epoch"]:
            entry = signal["entry_price"]
            direction = signal["type"]
            
            # Determine outcome
            outcome = "TIE"
            if direction == "CALL":
                if completed_close > entry:
                    outcome = "WON"
                elif completed_close < entry:
                    outcome = "LOST"
            elif direction == "PUT":
                if completed_close < entry:
                    outcome = "WON"
                elif completed_close > entry:
                    outcome = "LOST"

            logger.info(f"🔔 Signal {signal['id']} ({pair_display} {direction}) expired. Entry: {entry}, Expiry: {completed_close}. Outcome: {outcome}")
            
            # Update Database
            database.update_signal_outcome(
                signal_id=signal["id"],
                expiry_price=completed_close,
                outcome=outcome
            )

            # Send Telegram Outcome Update
            notifier.send_telegram_outcome(
                pair_display=pair_display,
                direction=direction,
                entry_price=entry,
                expiry_price=completed_close,
                outcome=outcome
            )
        else:
            remaining.append(signal)

    active_signals_tracker[pair] = remaining

async def database_cleanup_scheduler():
    """
    Runs every 24 hours to delete signals older than 30 days (1 month).
    """
    while True:
        logger.info("Running scheduled database cleanup...")
        database.delete_old_signals()
        # Sleep for 24 hours
        await asyncio.sleep(24 * 3600)

from tiingo_ws import LATEST_PRICES, tiingo_websocket_loop

class HealthCheckHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        # Handle CORS
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        
        if self.path == "/api/prices":
            import json
            self.wfile.write(json.dumps(LATEST_PRICES).encode('utf-8'))
        else:
            self.wfile.write(b'{"status": "ok", "message": "Quantum Bot Backend is running"}')
            
    def log_message(self, format, *args):
        # Suppress request logs to keep terminal output clean
        return

def populate_active_tracker_from_db():
    """
    Populates active_signals_tracker from Supabase on startup to handle server restarts.
    """
    if not database.supabase_client:
        logger.warning("Supabase client not active. Skipping active tracker population.")
        return

    try:
        response = database.supabase_client.table("signals").select("*").eq("status", "ACTIVE").execute()
        active_signals = response.data or []
        
        for sig in active_signals:
            pair = sig["pair"]
            if pair in active_signals_tracker:
                expiry_dt = datetime.fromisoformat(sig["expiry_time"].replace("Z", "+00:00"))
                expiry_epoch = int(expiry_dt.timestamp())
                
                active_signals_tracker[pair].append({
                    "id": sig["id"],
                    "type": sig["type"],
                    "entry_price": float(sig["entry_price"]),
                    "expiry_epoch": expiry_epoch
                })
        logger.info(f"Loaded {len(active_signals)} active signals from Supabase database on startup.")
    except Exception as e:
        logger.error(f"Error populating active tracker from Supabase: {e}")

def start_health_check_server():
    port = int(os.getenv("PORT", 8080))
    server = http.server.HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    logger.info(f"Started Health Check Server on port {port}")
    server.serve_forever()

async def main():
    logger.info("Starting Binary Options Signal Generator Backend...")
    
    # 1. Start HTTP Health Check Server in a background thread for Render Port Binding
    threading.Thread(target=start_health_check_server, daemon=True).start()
    
    # 1.5. Populate active tracker from DB to handle server restarts
    populate_active_tracker_from_db()
    
    # 2. Run database cleanup once on startup
    database.delete_old_signals()

    # 3. Start the database cleanup scheduler in the background
    asyncio.create_task(database_cleanup_scheduler())
    
    # 3.5 Start the Tiingo WebSocket loop to maintain live prices in memory
    asyncio.create_task(tiingo_websocket_loop())

    # 4. Initialize and run the TradingView Data Feed
    feed = TVDataFeed(pairs=config.MONITORED_PAIRS, callback=handle_candle_completed)
    await feed.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Backend stopped manually.")
